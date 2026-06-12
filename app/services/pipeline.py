"""流水线编排器：ThreadPoolExecutor 并行 + 阶段编排 + 日志 + 重试。"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

from app.config import Config, get_config
from app.database import (
    get_db,
    get_job,
    increment_retry,
    update_job_fields,
    update_job_status,
)
from app.models.job import JobStatus, Stage
from app.ui_copy import ProgressMsg, status_label
from app.services.platform_detector import detect_platform, normalize_url
from app.services.downloader import download
from app.services.xhs_cookie import resolve_xhs_cookie
from app.services.extractor import extract_audio
from app.services.transcriber import transcribe_tingwu
from app.services.ocr import list_image_files, ocr_images
from app.services.summarizer import summarize

# SSE 事件回调类型
SSECallback = Callable[[str, str, dict], None]


class PipelineManager:
    """管理所有流水线任务的执行、取消和重试。"""

    def __init__(self, config: Config | None = None):
        self.config = config or get_config()
        self._executor = ThreadPoolExecutor(max_workers=self.config.max_workers)
        self._futures: dict[str, Future] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._recover_stale_jobs()
        # SSE 订阅者: {job_id: [callback, ...]}
        self._sse_subscribers: dict[str, list[SSECallback]] = {}
        # 全局 SSE 订阅者 (dashboard 单连接)
        self._global_sse_subscribers: list[SSECallback] = []
        self._sse_lock = threading.Lock()
        # 启动存储清理线程
        if self.config.storage_retention_days > 0:
            self._start_cleanup_thread()

    def _recover_stale_jobs(self) -> None:
        """服务重启后，将长时间无更新的进行中任务标为失败，避免界面一直「下载中」。"""
        cfg = self.config
        minutes = cfg.stale_job_minutes
        conn = get_db(cfg.db_path)
        try:
            rows = conn.execute(
                "SELECT id, status, current_stage FROM jobs WHERE status IN "
                "('pending','downloading','extracting','transcribing','summarizing') "
                "AND updated_at < datetime('now', ?)",
                (f"-{minutes} minutes",),
            ).fetchall()
            for row in rows:
                job_id = row["id"]
                future = self._futures.get(job_id)
                if future is not None and not future.done():
                    continue
                msg = (
                    f"任务已中断或超时（超过 {minutes} 分钟无进度，常见于 Docker 重启）。"
                    "请在本页点击「重试」；B站若已下载分段将自动合并后继续。"
                )
                update_job_status(
                    conn,
                    job_id,
                    JobStatus.FAILED.value,
                    error_message=msg,
                    error_stage="interrupted",
                )
                logging.getLogger("app").warning(
                    "Recovered stale job | job_id=%s status=%s", job_id, row["status"]
                )
            if rows:
                conn.commit()
        finally:
            conn.close()

    def shutdown(self, wait: bool = True, timeout: float = 30):
        """Graceful shutdown: cancel pending futures and wait for running ones."""
        for evt in self._cancel_events.values():
            evt.set()
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def submit(self, job_id: str, url: str, downloader: str = "auto") -> None:
        """提交任务到线程池。"""
        self._cancel_events[job_id] = threading.Event()
        future = self._executor.submit(self._run_pipeline, job_id, url, downloader)
        self._futures[job_id] = future
        self._broadcast(job_id, "job_created", {
            "job_id": job_id,
            "url": url,
            "status": JobStatus.PENDING.value,
            "status_label": status_label(JobStatus.PENDING.value),
            "progress_pct": 0,
            "stage": "排队中",
        })

    def retry(self, job_id: str) -> str | None:
        """重试失败任务，返回 None 如果任务不存在或不是失败状态。"""
        conn = get_db(self.config.db_path)
        job = get_job(conn, job_id)
        if not job or job["status"] != JobStatus.FAILED.value:
            conn.close()
            return None

        increment_retry(conn, job_id)
        conn.commit()
        conn.close()

        self.submit(job_id, job["url"], "auto")
        return job_id

    def cancel(self, job_id: str) -> bool:
        """取消进行中的任务（发信号终止子进程）。"""
        cancel_event = self._cancel_events.get(job_id)
        if cancel_event:
            cancel_event.set()

        future = self._futures.get(job_id)
        if future and not future.done():
            future.cancel()
            conn = get_db(self.config.db_path)
            update_job_status(conn, job_id, JobStatus.FAILED.value,
                              error_message="用户已取消",
                              error_stage="user_cancel")
            conn.commit()
            conn.close()
            self._broadcast(job_id, "cancelled", {"status": "failed", "message": "已取消"})
            return True
        return False

    # ---- SSE 订阅 ----

    def subscribe(self, job_id: str, callback: SSECallback):
        with self._sse_lock:
            if job_id not in self._sse_subscribers:
                self._sse_subscribers[job_id] = []
            self._sse_subscribers[job_id].append(callback)

    def unsubscribe(self, job_id: str, callback: SSECallback):
        with self._sse_lock:
            subs = self._sse_subscribers.get(job_id, [])
            if callback in subs:
                subs.remove(callback)

    def subscribe_to_all(self, callback: SSECallback):
        with self._sse_lock:
            self._global_sse_subscribers.append(callback)

    def unsubscribe_from_all(self, callback: SSECallback):
        with self._sse_lock:
            if callback in self._global_sse_subscribers:
                self._global_sse_subscribers.remove(callback)

    def _broadcast(self, job_id: str, event: str, data: dict):
        with self._sse_lock:
            subs = list(self._sse_subscribers.get(job_id, []))
            global_subs = list(self._global_sse_subscribers)
        for cb in subs:
            try:
                cb(job_id, event, data)
            except Exception:
                logging.getLogger("app").warning(
                    "SSE broadcast error | job_id=%s event=%s", job_id, event, exc_info=True)
        for cb in global_subs:
            try:
                cb(job_id, event, data)
            except Exception:
                logging.getLogger("app").warning(
                    "Global SSE broadcast error | job_id=%s event=%s", job_id, event, exc_info=True)

    # ---- 流水线主逻辑 ----

    def _run_pipeline(self, job_id: str, url: str, downloader: str):
        """在后台线程中执行完整流水线。"""
        cfg = self.config
        conn = get_db(cfg.db_path)

        # 创建任务日志文件
        job_storage = os.path.join(cfg.storage_dir, job_id)
        os.makedirs(job_storage, exist_ok=True)
        log_path = os.path.join(job_storage, "job.log")
        job_logger = self._setup_job_logger(job_id, log_path)
        job_logger.info("Pipeline started | url=%s downloader=%s", url, downloader)

        update_job_fields(conn, job_id, storage_dir=job_storage)

        def progress(conn, job_id, stage, pct, msg, status, **extra):
            """更新进度并广播 SSE。"""
            try:
                update_job_status(conn, job_id, status.value, stage.label_cn, pct)
                conn.commit()
                self._broadcast(job_id, "progress", {
                    "job_id": job_id,
                    "status": status.value,
                    "status_label": status_label(status.value),
                    "stage": stage.label_cn,
                    "progress_pct": pct,
                    "message": msg,
                    **extra,
                })
            except Exception:
                pass

        def cb(stage: Stage, pct: int, msg: str, **extra):
            progress(conn, job_id, stage, pct, msg, stage.status, **extra)

        def fail(error_stage: str, error_msg: str):
            job_logger.error("Pipeline FAILED at %s: %s", error_stage, error_msg)
            job_logger.error("Traceback:\n%s", traceback.format_exc())
            update_job_status(conn, job_id, JobStatus.FAILED.value,
                              error_message=error_msg, error_stage=error_stage)
            conn.commit()
            self._broadcast(job_id, "failed", {
                "job_id": job_id,
                "status": "failed",
                "status_label": status_label("failed"),
                "error_message": error_msg,
                "error_stage": error_stage,
            })

        cancel_evt = self._cancel_events.get(job_id, threading.Event())

        def is_cancelled():
            return cancel_evt.is_set()

        try:
            if is_cancelled(): raise RuntimeError("User cancelled")

            # ---- Stage 0: 平台检测 ----
            rule, match = detect_platform(url)
            effective_downloader = downloader if downloader != "auto" else rule.default_downloader
            normalized_url = normalize_url(url, rule)
            job_logger.info("Platform detected | rule=%s match=%s downloader=%s url=%s",
                            rule.key, match, effective_downloader, normalized_url)
            update_job_fields(conn, job_id,
                              url=normalized_url,
                              platform=rule.key, downloader=effective_downloader, media_type=rule.media_type)
            cb(Stage.PLATFORM_DETECT, 3, f"平台: {rule.name} → {effective_downloader}")

            # ---- Stage 1: 下载 ----
            cb(Stage.DOWNLOAD, 5, ProgressMsg.DOWNLOAD_START)
            dl_result = download(
                url=normalized_url,
                rule=rule,
                downloader=effective_downloader,
                output_dir=job_storage,
                job_id=job_id,
                logger=job_logger,
                progress_cb=lambda p, m: cb(Stage.DOWNLOAD, 5 + int(p * 0.20), m),
                cancel_event=cancel_evt,
                cookies_file=cfg.cookies_file,
                cookies_from_browser=cfg.cookies_from_browser,
                xhs_cookie=resolve_xhs_cookie(cfg),
                proxy=cfg.https_proxy,
            )
            title = dl_result.get("title") or "Unknown"
            duration = dl_result.get("duration_sec")

            fields = {"title": title, "duration_sec": duration}
            if dl_result.get("video_path"):
                fields["video_path"] = dl_result["video_path"]
            if dl_result.get("images_dir"):
                fields["images_dir"] = dl_result["images_dir"]
            update_job_fields(conn, job_id, **fields)
            cb(Stage.DOWNLOAD, 25, f"{ProgressMsg.DOWNLOAD_DONE}: {title}", title=title)

            if is_cancelled(): raise RuntimeError("User cancelled")

            has_video = bool(dl_result.get("video_path"))
            has_images = bool(
                dl_result.get("images_dir")
                and list_image_files(dl_result["images_dir"])
            )

            # ---- Stage 2: 提取音频（视频笔记或含视频文件）----
            if has_video:
                cb(Stage.EXTRACT, 25, ProgressMsg.EXTRACT_START)
                audio_path = extract_audio(
                    video_path=dl_result["video_path"],
                    output_dir=job_storage,
                    job_id=job_id,
                    logger=job_logger,
                    progress_cb=lambda p, m: cb(Stage.EXTRACT, 25 + int(p * 0.05), m),
                    cancel_event=cancel_evt,
                )
                update_job_fields(conn, job_id, audio_path=audio_path)
                cb(Stage.EXTRACT, 30, ProgressMsg.EXTRACT_DONE)

            if is_cancelled(): raise RuntimeError("User cancelled")

            # ---- Stage 3: 转录（视频）----
            if has_video:
                cb(Stage.TRANSCRIBE, 30, ProgressMsg.TRANSCRIBE_START)
                tr_progress = lambda p, m: cb(Stage.TRANSCRIBE, 30 + int(p * 0.50), m)
                if cfg.transcribe_backend == "whisper":
                    from app.services.whisper_transcriber import transcribe_whisper
                    tr_result = transcribe_whisper(
                        audio_path=audio_path,
                        output_dir=job_storage,
                        job_id=job_id,
                        logger=job_logger,
                        progress_cb=tr_progress,
                        model=cfg.whisper_model,
                        device=cfg.whisper_device,
                        compute_type=cfg.whisper_compute_type,
                    )
                else:
                    tr_result = transcribe_tingwu(
                        audio_path=audio_path,
                        output_dir=job_storage,
                        job_id=job_id,
                        api_key="",  # 不再使用 DashScope
                        logger=job_logger,
                        progress_cb=tr_progress,
                        poll_interval=cfg.tingwu_poll_interval,
                        poll_timeout=cfg.tingwu_poll_timeout,
                        app_key=cfg.tingwu_app_key,
                        ak_id=cfg.alibaba_access_key_id,
                        ak_secret=cfg.alibaba_access_key_secret,
                        oss_endpoint=cfg.oss_endpoint,
                        oss_bucket=cfg.oss_bucket,
                    )
                update_job_fields(conn, job_id,
                                  transcript_path=tr_result["transcript_path"],
                                  transcript_text=tr_result["transcript_text"])
                transcript_text = tr_result["transcript_text"]
                cb(Stage.TRANSCRIBE, 80, f"{ProgressMsg.TRANSCRIBE_DONE} ({len(transcript_text)} 字)")

                # ---- Stage 4: 摘要 ----
                if not transcript_text.strip():
                    job_logger.warning("Transcription returned empty text, skipping summarization")
                    update_job_fields(conn, job_id, summary_text="（转录未产生文本，跳过摘要）")
                    cb(Stage.SUMMARIZE, 100, ProgressMsg.SUMMARIZE_SKIP_EMPTY)
                else:
                    cb(Stage.SUMMARIZE, 80, ProgressMsg.SUMMARIZE_START)
                    result = summarize(
                        transcript_text=transcript_text,
                        title=title,
                        output_dir=job_storage,
                        job_id=job_id,
                        logger=job_logger,
                        progress_cb=lambda p, m: cb(Stage.SUMMARIZE, 80 + int(p * 0.20), m),
                        media_type="video",
                    )
                    update_job_fields(
                        conn,
                        job_id,
                        summary_path=result.summary_path,
                        summary_text=result.summary_display,
                    )
            elif has_images:
                # ---- Stage 3: OCR 文字识别 ----
                cb(Stage.OCR, 30, ProgressMsg.OCR_START)
                ocr_result = ocr_images(
                    images_dir=dl_result["images_dir"],
                    output_dir=job_storage,
                    job_id=job_id,
                    ak_id=cfg.alibaba_access_key_id,
                    ak_secret=cfg.alibaba_access_key_secret,
                    logger=job_logger,
                    progress_cb=lambda p, m: cb(Stage.OCR, 30 + int(p * 0.50), m),
                    action="RecognizeAdvanced",
                )
                ocr_text = ocr_result["ocr_text"]
                update_job_fields(conn, job_id,
                                  transcript_path=ocr_result["ocr_path"],
                                  transcript_text=ocr_text)
                cb(Stage.OCR, 80, f"{ProgressMsg.OCR_DONE} ({len(ocr_text)} 字)")

                if is_cancelled(): raise RuntimeError("User cancelled")

                # ---- Stage 4: 摘要 ----
                if not ocr_text.strip():
                    job_logger.warning("OCR returned empty text, skipping summarization")
                    update_job_fields(conn, job_id, summary_text="（图片未识别到文字，跳过摘要）")
                    cb(Stage.SUMMARIZE, 100, ProgressMsg.OCR_SKIP_EMPTY)
                else:
                    cb(Stage.SUMMARIZE, 80, ProgressMsg.SUMMARIZE_START)
                    result = summarize(
                        transcript_text=ocr_text,
                        title=title,
                        output_dir=job_storage,
                        job_id=job_id,
                        logger=job_logger,
                        progress_cb=lambda p, m: cb(Stage.SUMMARIZE, 80 + int(p * 0.20), m),
                        media_type="image_text",
                        image_count=len(ocr_result["results"]),
                        images=list_image_files(dl_result["images_dir"]),
                    )
                    update_job_fields(
                        conn,
                        job_id,
                        summary_path=result.summary_path,
                        summary_text=result.summary_display,
                    )
            else:
                raise RuntimeError(
                    "下载完成但未找到可处理的图片或视频。"
                    "若为小红书链接，请同步 Cookie 后重试。"
                )

            # ---- 完成 ----
            # 写入 job_info.json
            job_row = get_job(conn, job_id)
            info_path = os.path.join(job_storage, "job_info.json")
            with open(info_path, "w", encoding="utf-8") as f:
                json.dump({
                    "id": job_id,
                    "url": url,
                    "platform": rule.key,
                    "title": title,
                    "media_type": rule.media_type,
                    "created_at": job_row.get("created_at") if job_row else None,
                }, f, ensure_ascii=False, indent=2)

            # 导出 MAGI 标准格式文件
            from app.services.magi_exporter import export_for_magi
            try:
                magi_path = export_for_magi(job_row or {}, job_storage)
                if magi_path:
                    job_logger.info("MAGI export written: %s", magi_path)
            except Exception:
                job_logger.warning("MAGI export failed (non-fatal)", exc_info=True)

            # 向量索引：跨任务语义检索（best-effort，未配置 embedder 时自动跳过）
            try:
                from app.services.vectorstore import get_vector_store
                vs = get_vector_store()
                if vs.enabled:
                    idx_text = (job_row or {}).get("summary_text") or \
                        (job_row or {}).get("transcript_text") or ""
                    if idx_text and vs.index(job_id, idx_text, title=title):
                        job_logger.info("向量索引完成 | job_id=%s", job_id)
            except Exception:
                job_logger.warning("向量索引失败 (non-fatal)", exc_info=True)

            update_job_status(conn, job_id, JobStatus.COMPLETED.value, None, 100)
            conn.commit()
            job_logger.info("Pipeline COMPLETED")
            self._broadcast(job_id, "completed", {
                "job_id": job_id,
                "status": "completed",
                "status_label": status_label("completed"),
                "progress_pct": 100,
            })

            # 清理 SSE 订阅者
            with self._sse_lock:
                self._sse_subscribers.pop(job_id, None)

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            error_stage = "unknown"
            # 根据当前 progress 判断失败阶段
            err_tb = traceback.format_exc()
            job_logger.error("Exception: %s\n%s", error_msg, err_tb)

            # 尝试推断失败阶段
            current_job = get_job(conn, job_id)
            if current_job:
                pct = current_job.get("progress_pct", 0)
                if pct < 5:
                    error_stage = "detecting"
                elif pct < 25:
                    error_stage = "downloading"
                elif pct < 30:
                    error_stage = "extracting"
                elif pct < 80:
                    error_stage = "transcribing"
                else:
                    error_stage = "summarizing"

            fail(error_stage, error_msg)

        finally:
            conn.close()

    def _setup_job_logger(self, job_id: str, log_path: str) -> logging.Logger:
        logger = logging.getLogger(f"job.{job_id}")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s"
        ))
        logger.addHandler(fh)
        logger.propagate = False
        return logger

    def _start_cleanup_thread(self):
        """启动后台线程，定期清理过期已归档任务的存储目录。"""
        import time as _time

        def _cleanup_loop():
            while True:
                _time.sleep(3600 * 6)  # 每 6 小时检查一次
                try:
                    self._cleanup_expired_storage()
                except Exception:
                    logging.getLogger("app").warning("Storage cleanup error", exc_info=True)

        t = threading.Thread(target=_cleanup_loop, daemon=True, name="storage-cleanup")
        t.start()

    def _cleanup_expired_storage(self):
        """删除超过保留期的已归档任务存储。"""
        cfg = self.config
        conn = get_db(cfg.db_path)
        cutoff = f"-{cfg.storage_retention_days} days"
        rows = conn.execute(
            "SELECT id, storage_dir FROM jobs "
            "WHERE is_archived = 1 AND updated_at < datetime('now', ?) AND storage_dir IS NOT NULL",
            (cutoff,),
        ).fetchall()
        for row in rows:
            storage_dir = row["storage_dir"]
            if storage_dir and os.path.exists(storage_dir):
                shutil.rmtree(storage_dir, ignore_errors=True)
                logging.getLogger("app").info("Cleaned up expired storage: %s", storage_dir)
        conn.close()


# 全局单例
_pipeline_manager: PipelineManager | None = None


def get_pipeline_manager() -> PipelineManager:
    global _pipeline_manager
    if _pipeline_manager is None:
        _pipeline_manager = PipelineManager()
    return _pipeline_manager
