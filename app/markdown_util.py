"""Markdown 渲染（摘要展示，带 XSS 过滤）。"""
from __future__ import annotations

import bleach
import markdown


import re

_ALLOWED_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "ul", "ol", "li",
    "strong", "em", "b", "i", "code", "pre", "blockquote",
    "a",
]
_ALLOWED_ATTRS = {"a": ["href", "title"]}

# 摘要展示文本里的小节头形如「【章节】」「【实体】」「【行动项】」「【标签】 …」
_SECTION_RE = re.compile(r"^【(.+?)】\s*(.*)$")


def render_summary_html(text: str | None) -> str:
    if not text or not text.strip():
        return ""
    raw = text.strip()
    if not any(raw.startswith(p) for p in ("#", "-", "*", "`")) and "###" not in raw:
        return _plain_summary_html(raw)
    html = markdown.markdown(raw, extensions=["extra", "nl2br"])
    return bleach.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)


def _plain_summary_html(text: str) -> str:
    blocks: list[str] = []
    bullet_buf: list[str] = []
    para_buf: list[str] = []

    def flush_bullets() -> None:
        nonlocal bullet_buf
        if bullet_buf:
            items = "".join(f"<li>{bleach.clean(line, strip=True)}</li>" for line in bullet_buf)
            blocks.append(f"<ul>{items}</ul>")
            bullet_buf = []

    def flush_para() -> None:
        nonlocal para_buf
        if para_buf:
            body = bleach.clean("\n".join(para_buf), strip=True)
            blocks.append(f"<p>{body.replace(chr(10), '<br>')}</p>")
            para_buf = []

    for line in text.splitlines():
        stripped = line.strip()
        section = _SECTION_RE.match(stripped)
        if section:
            flush_bullets()
            flush_para()
            label = bleach.clean(section.group(1), strip=True)
            rest = section.group(2).strip()
            if label == "标签" and rest:
                chips = "".join(
                    f'<span class="sum-tag">{bleach.clean(t.lstrip("#"), strip=True)}</span>'
                    for t in rest.split() if t.strip()
                )
                blocks.append(f'<div class="sum-tags">{chips}</div>')
            else:
                blocks.append(f'<h4 class="sum-section">{label}</h4>')
                if rest:
                    para_buf.append(rest)
            continue
        if stripped.startswith("• ") or stripped.startswith("- "):
            flush_para()
            bullet_buf.append(stripped[2:].strip())
            continue
        if not stripped:
            flush_bullets()
            flush_para()
            continue
        flush_bullets()
        para_buf.append(stripped)

    flush_bullets()
    flush_para()
    return "".join(blocks)
