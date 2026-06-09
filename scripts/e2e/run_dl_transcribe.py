"""E2E 编排（前半段）：真实 下载 → 提取音频 → 本地 Whisper 转录。

复用 app/services 里项目真实的下载/提取/转录代码，不走 mock。
摘要（LLM）步骤由编排者（子代理扮演 LLM）在本脚本之外完成，以实现"零付费 key 跑通"。

用法：
    ./.venv/bin/python scripts/e2e/run_dl_transcribe.py <URL> [输出目录]

输出：在 输出目录 下产生 video.* / audio.wav / transcript.txt，并打印转录字数。
"""
from __future__ import annotations

import logging
import os
import sys
import threading


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: run_dl_transcribe.py <URL> [输出目录]")
        return 2
    url = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join("storage", "e2e")
    os.makedirs(out_dir, exist_ok=True)

    # 让 app.config 等可导入
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)

    from app.config import get_config
    from app.services.platform_detector import detect_platform, normalize_url
    from app.services.downloader import download
    from app.services.extractor import extract_audio
    from app.services.whisper_transcriber import transcribe_whisper

    cfg = get_config()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("e2e")
    job_id = "e2e"
    cancel = threading.Event()

    def prog(stage):
        return lambda p, m: log.info("[%s] %s%% %s", stage, p, m)

    rule, match = detect_platform(url)
    nurl = normalize_url(url, rule)
    eff = rule.default_downloader
    log.info("平台=%s 匹配=%s 下载器=%s url=%s", rule.key, match, eff, nurl)

    dl = download(
        url=nurl, rule=rule, downloader=eff, output_dir=out_dir, job_id=job_id,
        logger=log, progress_cb=prog("download"), cancel_event=cancel,
        cookies_file=cfg.cookies_file, cookies_from_browser=cfg.cookies_from_browser,
        proxy=cfg.https_proxy,
    )
    log.info("下载完成 | title=%s video=%s", dl.get("title"), dl.get("video_path"))

    video_path = dl.get("video_path")
    if not video_path:
        log.error("未得到视频文件（图文链接需另测）")
        return 1

    audio = extract_audio(video_path, out_dir, job_id, log, prog("extract"), cancel)
    log.info("音频提取完成 | %s", audio)

    tr = transcribe_whisper(
        audio_path=audio, output_dir=out_dir, job_id=job_id, logger=log,
        progress_cb=prog("transcribe"), model=cfg.whisper_model,
        device=cfg.whisper_device, compute_type=cfg.whisper_compute_type,
    )
    text = tr["transcript_text"]
    tpath = os.path.join(out_dir, "transcript.txt")
    with open(tpath, "w", encoding="utf-8") as f:
        f.write(text)
    log.info("转录完成 | 字数=%d | 文稿=%s", len(text), tpath)
    print("\n=== E2E 前半段完成 ===")
    print("title:", dl.get("title"))
    print("transcript_chars:", len(text))
    print("transcript_path:", tpath)
    print("preview:", text[:200].replace("\n", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
