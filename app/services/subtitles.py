"""字幕解析：把下载器附带的官方/AI 字幕(srt/vtt)还原为纯文本。

用途：B站(yutto)、YouTube(yt-dlp) 等平台常自带官方或 AI 生成字幕，质量通常远高于
本地 whisper-tiny 重转写。命中字幕时直接复用为 transcript，既省一次音频转写、又提质。

只处理 .srt / .vtt（逐行时间轴 + 文本）。刻意不处理 .ass —— B站 yutto 的 .ass 是弹幕
（观众评论流），不是语音字幕，当作转写文本会污染摘要。
"""
from __future__ import annotations

import os
import re

# 时间轴行：00:00:01,000 --> 00:00:03,500 （srt 用逗号，vtt 用点，均兼容）
_TS_LINE = re.compile(r"-->")
# vtt 内联标签 / 卡拉 OK 时间戳，如 <00:00:01.000><c> 词 </c>
_INLINE_TAG = re.compile(r"<[^>]+>")
# 按空行切分 cue 块（srt/vtt 的 cue 之间、以及与头部/NOTE/STYLE 块之间都以空行分隔）
_BLANK_SPLIT = re.compile(r"\r?\n[ \t]*\r?\n")


def parse_subtitle_text(content: str) -> str:
    """从 srt/vtt 文本内容提取纯文本（保留出现顺序），基于 cue 块结构而非逐行启发式。

    规则：按空行把内容切成块；只处理**含时间轴(`-->`)的块**，输出该块中时间轴行**之后**
    的正文行。这样：
    - WEBVTT 头、NOTE/STYLE/REGION 块（无时间轴）整块丢弃——无论单行还是多行、位于头部
      还是 cue 之间，都不会把元数据/续行漏进正文；
    - cue 序号(srt)与 cue 标识(vtt，位于时间轴行之前)不会被当正文；
    - 正文里的纯数字行（年份/价格/比分等）正常保留（它们在时间轴之后）。
    仅去掉「紧邻的完全重复行」（滚动字幕的相邻重复），保留非相邻的合法重复（副歌等）。
    """
    lines_out: list[str] = []
    prev = None
    for block in _BLANK_SPLIT.split(content):
        block_lines = block.splitlines()
        ts_idx = next((i for i, ln in enumerate(block_lines) if _TS_LINE.search(ln)), None)
        if ts_idx is None:
            continue  # 非 cue 块（头部 / NOTE / STYLE / REGION）整块跳过
        for raw in block_lines[ts_idx + 1:]:
            if _TS_LINE.search(raw):
                continue  # 容错：同块内出现的后续时间轴行（异常拼接）不当正文
            text = _INLINE_TAG.sub("", raw).strip()
            if not text:
                continue
            if text == prev:  # 仅去相邻完全重复
                continue
            lines_out.append(text)
            prev = text
    return "\n".join(lines_out).strip()


def parse_subtitle_file(path: str) -> str:
    """读取 srt/vtt 文件并解析为纯文本；非字幕/读取失败返回空串。"""
    if not path or not os.path.isfile(path):
        return ""
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".srt", ".vtt"):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return parse_subtitle_text(f.read())
    except OSError:
        return ""


# 字幕语种优先级（小写、精确匹配文件名里的语种段）：简体 → 通用中文 → 繁体 → 英文。
# 必须精确匹配——子串匹配会让 "zh" 吃掉 "zh-hans"/"zh-cn"/"zh-hant"，使后者成为死项。
_LANG_PRIORITY = ("zh-hans", "zh-cn", "zh", "zh-hant", "en", "en-us")
# 合法语种段形如 zh / en / zh-Hans / en-US（2-3 位主语种 + 可选子标签），
# 用于避免把含点号的标题尾段（如 My.Video.Title → "title"）误当语种。
_LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")


def _lang_tag(path: str) -> str:
    """取字幕文件名里的语种段，如 title.zh-Hans.srt → 'zh-hans'；无/非法语种段返回 ''。"""
    stem = os.path.basename(path).rsplit(".", 1)[0]  # 去掉 .srt/.vtt
    if "." not in stem:
        return ""
    seg = stem.rsplit(".", 1)[1].lower()
    return seg if _LANG_RE.match(seg) else ""


def find_subtitle_file(output_dir: str) -> str:
    """在下载目录中挑选最合适的字幕文件（.srt/.vtt，排除弹幕 .ass）。

    按语种偏好（简体中文 → 通用中文 → 繁体 → 英文 → 其他）精确选取；
    同语种取文件较大者（信息更全）。找不到返回空串。
    """
    if not os.path.isdir(output_dir):
        return ""
    candidates: list[str] = []
    for root, _, files in os.walk(output_dir):
        for fn in files:
            if fn.startswith("."):
                continue
            if os.path.splitext(fn)[1].lower() in (".srt", ".vtt"):
                candidates.append(os.path.join(root, fn))
    if not candidates:
        return ""

    def rank(p: str) -> tuple[int, int]:
        lang = _lang_tag(p)
        try:
            lang_rank = _LANG_PRIORITY.index(lang)  # 精确匹配语种段
        except ValueError:
            lang_rank = len(_LANG_PRIORITY)
        size = os.path.getsize(p) if os.path.isfile(p) else 0
        return (lang_rank, -size)  # 语种优先级升序、同级体积大者优先

    candidates.sort(key=rank)
    return candidates[0]
