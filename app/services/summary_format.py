"""摘要 JSON 解析、展示文本与 Markdown 文件生成。"""
from __future__ import annotations

import json
import re
from typing import Any

VIDEO_JSON_PROMPT = """你是一位专业的内容摘要助手。根据提供的视频或内容文稿，输出结构化中文摘要。

要求：
1. 使用简体中文
2. 只输出一个 JSON 对象，不要 Markdown、不要代码块标记、不要客套开场白
3. JSON 字段：
   - headline (字符串): 一句话概括
   - key_points (字符串数组): 3-5 条核心观点
   - summary (字符串): 一段完整详细摘要
   - quotes (字符串数组): 2-3 条值得注意的原文引用，无则 []
   - chapters (对象数组): 章节/时间轴，每项 {"time": "mm:ss 或留空", "title": "小节标题"}；无明显分段则 []
   - entities (对象): {"people": [], "orgs": [], "places": [], "terms": []}，分别为人物、机构、地点、专有名词/术语，无则各为 []
   - action_items (字符串数组): 可执行的行动项/建议/待办，无则 []
   - tags (字符串数组): 3-6 个主题标签（不带 # 号）
4. 简洁完整，保留重要专有名词；缺失的字段给空数组/空对象，不要编造
"""

IMAGE_JSON_PROMPT = """你是一位专业的内容整理助手。你将收到从小红书等图文帖子提取的图片内容（图片本身或其 OCR 文字），需忠实整理。

要求：
1. 使用简体中文
2. 只输出一个 JSON 对象，不要 Markdown、不要代码块标记、不要客套开场白
3. 图片顺序可能错乱——根据序号、步骤、时间线等线索排成正确顺序
4. 去除重复内容（含轻微差异的重复句）
5. 忠实原文，不添加、不发挥
6. JSON 字段：
   - headline (字符串): 帖子主题
   - body (字符串): 按正确顺序整理的正文
   - key_facts (字符串数组): 事实、数字、地点、价格、日期等，无则 []
   - entities (对象): {"people": [], "orgs": [], "places": [], "terms": []}，无则各为 []
   - tags (字符串数组): 3-6 个主题标签（不带 # 号）
7. 若图片顺序只能推测，在 body 末尾加一行：[注：图片顺序为推测]
"""

# ---- map-reduce 长文本分段提示词 ----

MAP_PROMPT = """你是一位专业的内容摘要助手。下面是一段长文稿的【第 {idx}/{total} 段】。
请用简体中文输出这一段的要点摘要（150-300 字），保留这一段中的关键事实、数字、人物/机构/地点、专有名词和任何特殊标记句。
只输出摘要正文，不要客套、不要 JSON、不要 Markdown 标记。"""

# reduce 阶段复用 VIDEO_JSON_PROMPT；下面这句作为前缀提示输入是"逐段摘要"。
REDUCE_PREFIX = "以下是同一篇长文稿按顺序切分后逐段生成的多段摘要，请归并为覆盖【全文】的结构化摘要（必须涵盖首段与尾段，保留所有特殊标记句与专有名词）：\n\n"

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


_ENTITY_LABELS = (("people", "人物"), ("orgs", "机构"), ("places", "地点"), ("terms", "术语"))


def _entity_lines(entities: Any) -> list[str]:
    """把 entities 对象渲染为 '人物：A、B' 形式的行；非 dict 或空则返回 []。"""
    if not isinstance(entities, dict):
        return []
    out: list[str] = []
    for key, label in _ENTITY_LABELS:
        vals = [str(v).strip() for v in (entities.get(key) or []) if str(v).strip()]
        if vals:
            out.append(f"{label}：{'、'.join(vals)}")
    return out


def _chapter_items(chapters: Any) -> list[str]:
    """章节 → 纯文本条目（无前缀符号）。展示与 Markdown 各自加前缀，避免两处重复逻辑。"""
    if not isinstance(chapters, list):
        return []
    out: list[str] = []
    for c in chapters:
        if not isinstance(c, dict):
            continue
        title = str(c.get("title") or "").strip()
        if not title:
            continue
        tm = str(c.get("time") or "").strip()
        out.append(f"[{tm}] {title}" if tm else title)
    return out


def _chapter_lines(chapters: Any) -> list[str]:
    """把 chapters 数组渲染为带项目符号的条目（展示用）；缺失则返回 []。"""
    return [f"• {x}" for x in _chapter_items(chapters)]


def _tags_list(tags: Any) -> list[str]:
    if not isinstance(tags, list):
        return []
    return [str(t).strip().lstrip("#") for t in tags if str(t).strip()]


def _extras_display(data: dict[str, Any]) -> list[str]:
    """章节/实体/行动项/标签 → 展示用纯文本行（缺失字段自动跳过，兼容旧摘要）。"""
    blocks: list[str] = []
    chap = _chapter_lines(data.get("chapters"))
    if chap:
        blocks.append("【章节】")
        blocks.extend(chap)
    ents = _entity_lines(data.get("entities"))
    if ents:
        blocks.append("【实体】")
        blocks.extend(ents)
    actions = _lines(data.get("action_items"))
    if actions:
        blocks.append("【行动项】")
        blocks.extend(actions)
    tags = _tags_list(data.get("tags"))
    if tags:
        blocks.append("【标签】 " + " ".join(f"#{t}" for t in tags))
    return blocks


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

    extras = _extras_display(data)
    if extras:
        if parts:
            parts.append("")
        parts.extend(extras)

    return "\n".join(parts).strip()


def _extras_markdown(data: dict[str, Any]) -> list[str]:
    """章节/实体/行动项/标签 → Markdown 小节（缺失字段自动跳过）。"""
    lines: list[str] = []
    chap_rows = [f"- {x}" for x in _chapter_items(data.get("chapters"))]
    if chap_rows:
        lines.extend(["## 章节", ""])
        lines.extend(chap_rows)
        lines.append("")
    ents = _entity_lines(data.get("entities"))
    if ents:
        lines.extend(["## 实体", ""])
        lines.extend(f"- {e}" for e in ents)
        lines.append("")
    actions = [str(a).strip() for a in (data.get("action_items") or []) if str(a).strip()]
    if actions:
        lines.extend(["## 行动项", ""])
        lines.extend(f"- {a}" for a in actions)
        lines.append("")
    tags = _tags_list(data.get("tags"))
    if tags:
        lines.extend(["## 标签", "", " ".join(f"#{t}" for t in tags), ""])
    return lines


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

    lines.extend(_extras_markdown(data))

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
