"""Markdown 渲染（摘要展示，带 XSS 过滤）。"""
from __future__ import annotations

import bleach
import markdown


_ALLOWED_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "ul", "ol", "li",
    "strong", "em", "b", "i", "code", "pre", "blockquote",
    "a",
]
_ALLOWED_ATTRS = {"a": ["href", "title"]}


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
