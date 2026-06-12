"""生成基准台测试样本。

- 长转录：约 40 万字符合成文稿，尾部埋入独特标记句，用于检测截断丢尾。
- 图片：用 Pillow 画几行中英文，模拟小红书图文，存 sample_img.png。

3.9 兼容。
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))

# 尾部标记句：只出现在文稿最末尾，超过 300k 截断阈值后会被丢掉。
TAIL_MARKER = "【尾部关键信息：项目代号 MAGI-OMEGA，发布日期 2026 年 7 月 1 日】"
# 头部标记句：用于对照（应始终保留）。
HEAD_MARKER = "【头部关键信息：本视频主题为内容搬运流水线的引擎选型】"

_FILLER = (
    "在这段内容里，主讲人详细讨论了内容搬运流水线的设计理念、抓取策略、"
    "转录质量、摘要结构化输出，以及多模态处理的种种权衡。"
    "他反复强调，超长视频的转录文本如果被硬截断，会丢失结尾处的关键结论。"
    "This is filler narration repeated to inflate the transcript length for the benchmark. "
)


def make_long_transcript(target_chars=400_000):
    """生成约 target_chars 字符的合成长转录，头尾各埋一个标记句。"""
    parts = [HEAD_MARKER, "\n\n"]
    body_target = target_chars - len(HEAD_MARKER) - len(TAIL_MARKER) - 8
    seg = _FILLER
    n = max(1, body_target // len(seg))
    # 每隔若干段插入一个递增的“章节”标记，便于人工核对分块覆盖
    chunk = []
    for i in range(n):
        if i % 200 == 0:
            chunk.append("\n第 {} 节。".format(i // 200 + 1))
        chunk.append(seg)
    parts.append("".join(chunk))
    parts.append("\n\n")
    parts.append(TAIL_MARKER)
    return "".join(parts)


def make_sample_image(path=None):
    """用 Pillow 画一张含中英文文字的合成图片，模拟小红书图文。"""
    from PIL import Image, ImageDraw, ImageFont

    if path is None:
        path = os.path.join(HERE, "sample_img.png")

    W, H = 800, 600
    img = Image.new("RGB", (W, H), (255, 248, 240))
    draw = ImageDraw.Draw(img)

    lines = [
        "小红书图文测试卡片",
        "MAGI Content Bench",
        "地点：上海 静安寺",
        "人均：￥128  评分：4.8",
        "营业时间 10:00 - 22:00",
        "Tip: native vision vs OCR",
    ]

    # 尝试加载中文字体，失败则退回默认字体（默认字体不一定渲染中文，
    # 但图片仍可用于多模态调用；OCR/视觉引擎对图像本身处理）。
    font = None
    for fp in [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 36)
                break
            except Exception:  # noqa: BLE001
                continue
    if font is None:
        font = ImageFont.load_default()

    y = 40
    for ln in lines:
        draw.text((40, y), ln, fill=(40, 40, 40), font=font)
        y += 80

    img.save(path)
    return path


if __name__ == "__main__":
    t = make_long_transcript()
    print("transcript chars:", len(t))
    print("head marker present:", HEAD_MARKER in t)
    print("tail marker present:", TAIL_MARKER in t)
    p = make_sample_image()
    print("image saved:", p)
