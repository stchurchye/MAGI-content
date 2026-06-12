"""摘要/多模态引擎对比基准台 —— 运行入口。

用法::

    .venv-bench/bin/python scripts/bench/run_bench.py

环境变量（可选，控制成本/范围）：
- BENCH_REAL_CHARS  长文真实调用时取前 N 字符（默认 200000，仍跨过 300k？否——
                    见下方说明：为同时验证“单次会截断丢尾”，单次引擎喂【完整 40 万】，
                    map-reduce 喂【截断到 BENCH_REAL_CHARS 的子集 + 尾标记】以省钱。）
- BENCH_FULL        设为 1 时所有引擎都喂完整 40 万字符（更贵）。

缺 key 的引擎自动 skipped。
"""
from __future__ import annotations

import os
import sys

# 加载 .env（python-dotenv），从项目根读取 DEEPSEEK_API_KEY 等
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:  # noqa: BLE001
    # 手动解析兜底
    envp = os.path.join(ROOT, ".env")
    if os.path.exists(envp):
        with open(envp, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, HERE)

import engines as E  # noqa: E402
import sample as S  # noqa: E402

from engines import (  # noqa: E402
    DeepSeekEngine,
    DeepSeekMapReduceEngine,
    ClaudeEngine,
    QwenVLEngine,
)


def _cost(meta, engine_cls):
    it = meta.get("input_tokens", -1)
    ot = meta.get("output_tokens", -1)
    if it < 0 or ot < 0:
        return None
    return it * engine_cls.PRICE_IN + ot * engine_cls.PRICE_OUT


def _summary_preview(data, media_type, n=200):
    if media_type == "image_text":
        s = (data.get("headline") or "") + " | " + (data.get("body") or "")
    else:
        kp = data.get("key_points") or []
        s = (data.get("headline") or "") + " | " + " ".join(str(x) for x in kp)
        s += " | " + (data.get("summary") or "")
    s = s.strip().replace("\n", " ")
    return s[:n]


def _tail_kept(data):
    """检测尾部标记的核心 token 是否出现在摘要相关字段里。"""
    blob = " ".join([
        str(data.get("headline", "")),
        str(data.get("summary", "")),
        str(data.get("body", "")),
        " ".join(str(x) for x in (data.get("key_points") or [])),
        " ".join(str(x) for x in (data.get("key_facts") or [])),
        " ".join(str(x) for x in (data.get("quotes") or [])),
    ])
    return "MAGI-OMEGA" in blob


def run_text_case(full_text, real_chars, force_full):
    print("\n" + "=" * 78)
    print("用例 1: 长视频转录 (media_type=video)")
    print("  完整文稿字符数: {:,}".format(len(full_text)))
    print("  300k 截断阈值: {:,}  (单次引擎会丢尾)".format(E.MAX_CHARS))
    truncated_input = full_text[:real_chars] + "\n\n" + S.TAIL_MARKER
    if force_full:
        truncated_input = full_text
    print("  map-reduce 实跑输入字符数: {:,} (省钱子集 + 尾标记)".format(len(truncated_input)))
    print("=" * 78)

    rows = []

    # A. DeepSeek 单次 —— 喂【完整 40 万】以真实复现 300k 截断丢尾
    eng = DeepSeekEngine()
    print("\n[运行] {} (输入完整 {:,} 字符)...".format(eng.name, len(full_text)))
    data = eng.summarize(full_text, media_type="video")
    rows.append((DeepSeekEngine, data))

    # B. DeepSeek map-reduce —— 喂省钱子集（含尾标记），分块归并
    eng = DeepSeekMapReduceEngine(chunk_chars=80_000)
    print("[运行] {} (输入 {:,} 字符, chunk=80k)...".format(eng.name, len(truncated_input)))
    data = eng.summarize(truncated_input, media_type="video")
    rows.append((DeepSeekMapReduceEngine, data))

    # C. Claude —— 缺 key 会 skipped
    eng = ClaudeEngine()
    inp = full_text if (force_full or eng.available()) else full_text
    print("[运行] {} ...".format(eng.name))
    data = eng.summarize(inp, media_type="video")
    rows.append((ClaudeEngine, data))

    # D. Qwen-VL —— 缺 key 会 skipped
    eng = QwenVLEngine()
    print("[运行] {} ...".format(eng.name))
    data = eng.summarize(truncated_input, media_type="video")
    rows.append((QwenVLEngine, data))

    _print_table(rows, "video")
    return rows


def run_image_case(image_path):
    print("\n" + "=" * 78)
    print("用例 2: 小红书图文 (media_type=image_text)")
    print("  图片: {}".format(image_path))
    print("=" * 78)

    rows = []
    # DeepSeek 无视觉：用空 OCR 文本占位（演示其无法处理纯视觉）
    ocr_stub = "（DeepSeek 无原生视觉，此处仅有占位 OCR 文本：小红书图文测试卡片）"

    eng = DeepSeekEngine()
    print("\n[运行] {} (仅文本/无视觉)...".format(eng.name))
    rows.append((DeepSeekEngine, eng.summarize(ocr_stub, media_type="image_text")))

    eng = ClaudeEngine()
    print("[运行] {} (原生视觉)...".format(eng.name))
    rows.append((ClaudeEngine, eng.summarize("", images=[image_path], media_type="image_text")))

    eng = QwenVLEngine()
    print("[运行] {} (原生视觉)...".format(eng.name))
    rows.append((QwenVLEngine, eng.summarize("", images=[image_path], media_type="image_text")))

    _print_table(rows, "image_text")
    return rows


def _print_table(rows, media_type):
    print("\n{:<22} {:<6} {:>8} {:>10} {:>10} {:>8} {:>12}".format(
        "引擎", "可用", "延迟s", "输入tok", "输出tok", "丢尾", "成本$"))
    print("-" * 90)
    for cls, data in rows:
        meta = data.get("_meta", {})
        avail = "否" if meta.get("skipped") else "是"
        lat = meta.get("latency_sec", 0)
        it = meta.get("input_tokens", -1)
        ot = meta.get("output_tokens", -1)
        cost = _cost(meta, cls)
        cost_s = "skip" if meta.get("skipped") else (
            "{:.5f}".format(cost) if cost is not None else "n/a")
        # 丢尾：仅 video 用例有意义
        if media_type == "video" and not meta.get("skipped") and not meta.get("error"):
            lost = "否(保留)" if _tail_kept(data) else "是(丢失)"
            # 单次引擎喂的是完整文稿，truncated=True 时预期丢尾
        else:
            lost = "-"
        print("{:<22} {:<6} {:>8} {:>10} {:>10} {:>8} {:>12}".format(
            meta.get("engine", cls.name)[:22], avail, lat,
            it if it >= 0 else "-", ot if ot >= 0 else "-", lost, cost_s))
        if meta.get("truncated"):
            print("    ! 输入被 300k 硬截断 (truncated=True)")
        if meta.get("chunks"):
            print("    · map-reduce 分块数: {}".format(meta["chunks"]))
        if meta.get("error"):
            print("    ERROR: {}".format(meta["error"]))
        if not meta.get("skipped") and not meta.get("error"):
            print("    摘要预览: {}".format(_summary_preview(data, media_type)))


def main():
    real_chars = int(os.environ.get("BENCH_REAL_CHARS", "200000"))
    force_full = os.environ.get("BENCH_FULL") == "1"

    print("生成测试样本...")
    full_text = S.make_long_transcript(400_000)
    print("  长转录: {:,} 字符  头标记={}  尾标记={}".format(
        len(full_text),
        S.HEAD_MARKER in full_text,
        S.TAIL_MARKER in full_text))
    img = S.make_sample_image()
    print("  图片: {}".format(img))

    run_text_case(full_text, real_chars, force_full)
    run_image_case(img)

    print("\n完成。缺 key 的引擎已标记 skipped。")
    print("补 key 后运行：")
    print("  ANTHROPIC_API_KEY=sk-ant-... .venv-bench/bin/python scripts/bench/run_bench.py   # 启用 Claude")
    print("  DASHSCOPE_API_KEY=sk-...     .venv-bench/bin/python scripts/bench/run_bench.py   # 启用 Qwen-VL")


if __name__ == "__main__":
    main()
