"""REST API + SSE + 页面路由。"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.config import get_config
from app.database import (
    ACTIVE_STATUSES,
    archive_job,
    count_jobs,
    count_stuck_jobs,
    count_search_jobs,
    delete_job,
    get_db,
    get_job,
    insert_job,
    list_active_jobs,
    list_jobs,
    search_jobs,
)
from app.markdown_util import render_summary_html
from app.models.job import Job
from app.services.cookie_sync import get_cookies_status, sync_xhs_cookie
from app.services.pipeline import get_pipeline_manager
from app.ui_copy import DOWNLOADER_OPTIONS, PLATFORM_FILTER_OPTIONS, timeline_for_media_type

router = APIRouter()

templates = None


@router.get("/health")
async def health():
    """健康检查端点。"""
    return {"status": "ok", "version": "0.2.0"}


@router.get("/api/search/semantic")
async def semantic_search(
    q: str = Query(..., min_length=1),
    k: int = Query(5, ge=1, le=20),
):
    """跨任务语义检索（向量库）。未配置 embedder 时 enabled=False。"""
    from app.services.vectorstore import get_vector_store

    vs = get_vector_store()
    if not vs.enabled:
        return {
            "enabled": False,
            "hits": [],
            "message": "语义检索未启用：设置 EMBEDDING_BACKEND=openai 与 EMBEDDING_API_KEY（或 =local 测试）",
        }
    hits = vs.search(q, k=k)
    return {
        "enabled": True,
        "hits": [
            {
                "job_id": h.job_id,
                "title": h.title,
                "excerpt": h.excerpt,
                "distance": round(h.distance, 4),
            }
            for h in hits
        ],
    }


def _get_templates():
    global templates
    if templates is None:
        from fastapi.templating import Jinja2Templates
        tmpl_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates")
        templates = Jinja2Templates(directory=os.path.abspath(tmpl_dir))
        templates.env.globals["downloader_options"] = DOWNLOADER_OPTIONS
    return templates


def _job_context(job: Job, log_text: str = "") -> dict:
    return {
        "job": job,
        "log_text": log_text,
        "summary_html": render_summary_html(job.summary_display),
        "timeline_stages": list(timeline_for_media_type(job.media_type)),
    }


_FILE_KIND_MAP = {
    "video": "video_path",
    "audio": "audio_path",
    "transcript": "transcript_path",
    "summary": "summary_path",
    "danmaku": "danmaku_path",
}


# ========== Cookie ==========


class XhsCookieSyncBody(BaseModel):
    browser: str = Field(default="chrome", description="chrome / safari / edge 等")
    cookie: str | None = Field(default=None, description="手动粘贴的 Cookie 全文")


@router.get("/api/cookies/status")
async def cookies_status():
    """各平台 Cookie 配置状态（平台间不共用）。"""
    return get_cookies_status(get_config())


@router.post("/api/cookies/sync/xiaohongshu")
async def cookies_sync_xiaohongshu(body: XhsCookieSyncBody):
    """同步小红书 Cookie：本机从浏览器读取，或粘贴写入文件。"""
    cfg = get_config()
    try:
        return sync_xhs_cookie(
            cfg,
            browser=body.browser or None,
            manual_cookie=body.cookie,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ========== 页面路由 ==========

HISTORY_EXCLUDE_STATUSES = (*ACTIVE_STATUSES, "failed")


def _paginate(total: int, page: int, per_page: int) -> int:
    return max(1, -(-total // per_page))


def _list_jobs_page(
    conn,
    *,
    page: int,
    per_page: int,
    q: str | None,
    platform: str | None,
    status: str | None,
    is_archived: bool | None,
    exclude_statuses: tuple[str, ...] | None,
) -> tuple[list[dict], int]:
    offset = (page - 1) * per_page
    if q:
        rows = search_jobs(
            conn, q,
            status=status,
            is_archived=is_archived,
            platform=platform or None,
            exclude_statuses=exclude_statuses,
            limit=per_page,
            offset=offset,
        )
        total = count_search_jobs(
            conn, q,
            status=status,
            is_archived=is_archived,
            platform=platform or None,
            exclude_statuses=exclude_statuses,
        )
    else:
        rows = list_jobs(
            conn,
            status=status,
            is_archived=is_archived,
            platform=platform or None,
            exclude_statuses=exclude_statuses,
            limit=per_page,
            offset=offset,
        )
        total = count_jobs(
            conn,
            status=status,
            is_archived=is_archived,
            platform=platform or None,
            exclude_statuses=exclude_statuses,
        )
    return rows, total


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    page: int = Query(1, ge=1),
    q: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
):
    """首页：右侧同步 + 左侧正常历史（排除失败/归档/进行中）。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    per_page = 50

    active_rows = list_active_jobs(conn)
    failed_count = count_jobs(conn, status="failed", is_archived=False)
    stuck_count = count_stuck_jobs(conn, cfg.stale_job_minutes)

    rows, total = _list_jobs_page(
        conn,
        page=page,
        per_page=per_page,
        q=q,
        platform=platform,
        status=None,
        is_archived=False,
        exclude_statuses=HISTORY_EXCLUDE_STATUSES,
    )
    conn.close()

    active_jobs = [Job.from_row(r) for r in active_rows]
    list_jobs_result = [Job.from_row(r) for r in rows]

    tmpl = _get_templates()
    return tmpl.TemplateResponse(request, "index.html", {
        "active_jobs": active_jobs,
        "jobs": list_jobs_result,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": _paginate(total, page, per_page),
        "query": q or "",
        "platform_filter": platform or "",
        "failed_count": failed_count,
        "stuck_count": stuck_count,
        "stale_job_minutes": cfg.stale_job_minutes,
        "platform_filter_options": PLATFORM_FILTER_OPTIONS,
        "downloader_options": DOWNLOADER_OPTIONS,
    })


@router.get("/jobs/failed", response_class=HTMLResponse)
async def jobs_failed(
    request: Request,
    page: int = Query(1, ge=1),
    q: Optional[str] = Query(None),
):
    """失败任务独立页。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    per_page = 30
    rows, total = _list_jobs_page(
        conn,
        page=page,
        per_page=per_page,
        q=q,
        platform=None,
        status="failed",
        is_archived=False,
        exclude_statuses=None,
    )
    conn.close()

    tmpl = _get_templates()
    return tmpl.TemplateResponse(request, "jobs_queue.html", {
        "page_title": "失败任务",
        "page_subtitle": "FAILED",
        "jobs": [Job.from_row(r) for r in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": _paginate(total, page, per_page),
        "query": q or "",
        "list_path": "/jobs/failed",
        "empty_message": "暂无失败任务",
        "card_variant": "failed_log",
        "stale_job_minutes": cfg.stale_job_minutes,
    })


@router.get("/jobs/archived", response_class=HTMLResponse)
async def jobs_archived(
    request: Request,
    page: int = Query(1, ge=1),
    q: Optional[str] = Query(None),
):
    """已归档任务独立页。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    per_page = 30
    rows, total = _list_jobs_page(
        conn,
        page=page,
        per_page=per_page,
        q=q,
        platform=None,
        status=None,
        is_archived=True,
        exclude_statuses=None,
    )
    conn.close()

    tmpl = _get_templates()
    return tmpl.TemplateResponse(request, "jobs_queue.html", {
        "page_title": "已归档",
        "page_subtitle": "ARCHIVED",
        "jobs": [Job.from_row(r) for r in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": _paginate(total, page, per_page),
        "query": q or "",
        "list_path": "/jobs/archived",
        "empty_message": "暂无已归档任务",
    })


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: str):
    """任务详情页。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    row = get_job(conn, job_id)
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")

    job = Job.from_row(row)

    # 读取日志
    log_text = ""
    if job.storage_dir:
        log_path = os.path.join(job.storage_dir, "job.log")
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    log_text = f.read()
            except Exception:
                log_text = "[无法读取日志]"

    ctx = _job_context(job, log_text)
    tmpl = _get_templates()
    return tmpl.TemplateResponse(request, "job.html", ctx)


# ========== API 路由 ==========

@router.post("/api/jobs")
async def create_job(
    request: Request,
    url: str = Form(...),
    downloader: str = Form("auto"),
):
    """创建新任务。支持批量（每行一个 URL）。始终回到首页。"""
    from app.services.downloader import DOWNLOAD_PLUGINS
    if downloader not in ({"auto"} | set(DOWNLOAD_PLUGINS)):
        raise HTTPException(status_code=400, detail=f"未知下载器: {downloader}")

    cfg = get_config()
    pipeline = get_pipeline_manager()
    conn = get_db(cfg.db_path)

    urls = [u.strip() for u in url.splitlines() if u.strip()]

    # SSRF 守卫:所有下载的统一咽喉点。任一 URL 指向内网/保留地址/云元数据(或非白名单平台)
    # 即整批拒绝,不论下载器是否走代理。先校验再落库,避免脏 job。
    from app.services.url_guard import assert_download_url_allowed
    for u in urls:
        try:
            assert_download_url_allowed(u, allow_generic=cfg.allow_generic_download)
        except ValueError as e:
            conn.close()
            raise HTTPException(status_code=400, detail=f"URL 被拒: {u} — {e}") from e

    # 并发上限:活跃任务 + 本批超过上限即拒,防排队轰炸耗尽 CPU/磁盘/出网。
    active = len(list_active_jobs(conn))
    if active + len(urls) > cfg.max_active_jobs:
        conn.close()
        raise HTTPException(
            status_code=429,
            detail=f"活跃任务过多（{active}/{cfg.max_active_jobs}），请稍后再试",
        )

    created: list[dict] = []

    for u in urls:
        job_id = str(uuid.uuid4())
        insert_job(conn, job_id, u)
        conn.commit()
        pipeline.submit(job_id, u, downloader)
        created.append({"id": job_id, "url": u})

    conn.close()

    if "application/json" in request.headers.get("accept", "").lower():
        return {"jobs": created}
    return RedirectResponse(url="/", status_code=303)


_ALLOWED_UPLOAD_EXTS = {
    # 视频/音频
    ".mp4", ".mov", ".mkv", ".webm", ".flv", ".avi", ".m4v",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg",
    # 图片（与 ocr.IMAGE_EXTS 对齐）
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".avif", ".heic",
}


@router.post("/api/jobs/upload")
async def upload_job(request: Request, file: UploadFile = File(...)):
    """本地文件上传建任务：保存到 storage/{job_id}/，downloader=local 跳过下载。"""
    cfg = get_config()
    pipeline = get_pipeline_manager()

    # 防路径穿越：只取文件名部分
    safe_name = os.path.basename(file.filename or "upload")
    if not safe_name or safe_name in (".", ".."):
        safe_name = "upload.bin"
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in _ALLOWED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext or '(无扩展名)'}")

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(cfg.storage_dir, job_id)
    os.makedirs(job_dir, exist_ok=True)
    dest = os.path.join(job_dir, safe_name)

    # 大小上限：边写边累计，超限即中止并清理，避免超大文件耗尽磁盘
    max_bytes = cfg.max_upload_mb * 1024 * 1024
    written = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过上限 {cfg.max_upload_mb}MB",
                    )
                f.write(chunk)
    except Exception:
        # 任何异常（超限 / 客户端断连 / 磁盘错误）都清理残留，避免孤儿文件堆积占盘
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    conn = get_db(cfg.db_path)
    insert_job(conn, job_id, dest)
    conn.commit()
    conn.close()
    pipeline.submit(job_id, dest, "local")

    if "application/json" in request.headers.get("accept", "").lower():
        return {"jobs": [{"id": job_id, "url": dest}]}
    return RedirectResponse(url="/", status_code=303)


@router.get("/api/jobs")
async def list_jobs_api(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    is_archived: Optional[bool] = Query(None),
):
    """任务列表 JSON。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    rows = list_jobs(conn, status=status, limit=limit, offset=offset, is_archived=is_archived)
    conn.close()
    return {"jobs": [Job.from_row(r).to_dict() for r in rows]}


@router.get("/api/jobs/count")
async def count_jobs_api(
    status: Optional[str] = Query(None),
    is_archived: Optional[bool] = Query(None),
):
    """任务总数。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    total = count_jobs(conn, status=status, is_archived=is_archived)
    conn.close()
    return {"total": total}


@router.get("/api/jobs/alert-summary")
async def jobs_alert_summary():
    """首页失败/卡住任务数量（供前端刷新告警条，避免重复累加）。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    failed_count = count_jobs(conn, status="failed", is_archived=False)
    stuck_count = count_stuck_jobs(conn, cfg.stale_job_minutes)
    conn.close()
    return {
        "failed_count": failed_count,
        "stuck_count": stuck_count,
        "stale_job_minutes": cfg.stale_job_minutes,
    }


@router.get("/api/jobs/search")
async def search_jobs_api(
    q: str = Query(..., min_length=1),
    status: Optional[str] = Query(None),
    is_archived: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """搜索任务。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    rows = search_jobs(conn, q, status=status, is_archived=is_archived, limit=limit, offset=offset)
    total = count_search_jobs(conn, q, status=status, is_archived=is_archived)
    conn.close()
    return {"jobs": [Job.from_row(r).to_dict() for r in rows], "total": total}


@router.get("/api/jobs/{job_id}/card", response_class=HTMLResponse)
async def job_card_html(
    request: Request,
    job_id: str,
    variant: Literal["list", "active"] = "list",
):
    """返回任务卡片 HTML 片段（供 SSE 更新使用）。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    row = get_job(conn, job_id)
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    job = Job.from_row(row)
    tmpl_name = "partials/task_card_active.html" if variant == "active" else "partials/task_card_list.html"
    tmpl = _get_templates()
    ctx = {"job": job, "stale_job_minutes": cfg.stale_job_minutes}
    return tmpl.TemplateResponse(request, tmpl_name, ctx)


@router.get("/api/jobs/{job_id}/fragment", response_class=HTMLResponse)
async def job_fragment_html(request: Request, job_id: str):
    """任务详情动态区域 HTML（完成后无刷新更新）。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    row = get_job(conn, job_id)
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    job = Job.from_row(row)
    tmpl = _get_templates()
    return tmpl.TemplateResponse(request, "partials/job_dynamic.html", _job_context(job))


@router.get("/api/jobs/{job_id}/files/{kind}")
async def download_job_file(job_id: str, kind: str):
    """下载任务产出文件。"""
    if kind not in _FILE_KIND_MAP:
        raise HTTPException(status_code=400, detail="不支持的文件类型")

    cfg = get_config()
    conn = get_db(cfg.db_path)
    row = get_job(conn, job_id)
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")

    job = Job.from_row(row)
    path = getattr(job, _FILE_KIND_MAP[kind], None)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="文件不存在")

    filename = os.path.basename(path)
    return FileResponse(path, filename=filename, media_type="application/octet-stream")


@router.get("/api/jobs/{job_id}")
async def get_job_api(job_id: str):
    """单个任务 JSON。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    row = get_job(conn, job_id)
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return Job.from_row(row).to_dict()


@router.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str):
    """重试失败任务。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    row = get_job(conn, job_id)
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    if row["status"] != "failed":
        raise HTTPException(
            status_code=400,
            detail=f"仅失败任务可重试（当前：{row['status']}）",
        )

    # 重试前重跑 SSRF 守卫(与 create_job 对齐):防止旧 job 的 URL 在守卫上线前入库、
    # 或 DNS 在此期间被改指向内网。
    from app.services.url_guard import assert_download_url_allowed
    try:
        assert_download_url_allowed(row["url"], allow_generic=cfg.allow_generic_download)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"URL 被拒: {row['url']} — {e}") from e

    pipeline = get_pipeline_manager()
    result = pipeline.retry(job_id)
    if result is None:
        raise HTTPException(status_code=409, detail="任务状态已变更，请刷新后重试")
    return {"status": "retrying", "job_id": result}


@router.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """取消进行中的任务。"""
    pipeline = get_pipeline_manager()
    ok = pipeline.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="任务不存在或已完成，无法取消")
    return {"status": "cancelled"}


@router.delete("/api/jobs/{job_id}")
async def delete_job_api(job_id: str):
    """删除任务及关联文件。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    row = get_job(conn, job_id)
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="任务不存在")

    # 删除存储目录
    job = Job.from_row(row)
    if job.storage_dir and os.path.exists(job.storage_dir):
        shutil.rmtree(job.storage_dir)

    delete_job(conn, job_id)
    conn.commit()
    conn.close()
    return {"status": "deleted"}


@router.post("/api/jobs/{job_id}/archive")
async def archive_job_api(job_id: str):
    """归档任务。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    row = get_job(conn, job_id)
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="任务不存在")
    archive_job(conn, job_id, archived=True)
    conn.commit()
    conn.close()
    return {"status": "archived"}


@router.post("/api/jobs/{job_id}/unarchive")
async def unarchive_job_api(job_id: str):
    """取消归档。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    row = get_job(conn, job_id)
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="任务不存在")
    archive_job(conn, job_id, archived=False)
    conn.commit()
    conn.close()
    return {"status": "unarchived"}


@router.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: str, request: Request):
    """SSE 实时进度推送（单任务）。"""
    pipeline = get_pipeline_manager()

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        async def _put(event: str, data: dict):
            await queue.put((event, data))

        def on_event(jid: str, event: str, data: dict):
            try:
                asyncio.run_coroutine_threadsafe(_put(event, data), loop)
            except Exception:
                import logging
                logging.getLogger("app").warning("SSE on_event failed", exc_info=True)

        pipeline.subscribe(job_id, on_event)

        try:
            # 先发当前状态
            cfg = get_config()
            conn = get_db(cfg.db_path)
            row = get_job(conn, job_id)
            conn.close()
            if row:
                job = Job.from_row(row)
                yield f"event: init\ndata: {json.dumps(job.to_dict())}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            pipeline.unsubscribe(job_id, on_event)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/api/active-jobs/stream")
async def stream_all_active(request: Request):
    """全局 SSE：一个连接推送所有活跃任务的事件。"""
    pipeline = get_pipeline_manager()

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        async def _put(event: str, data: dict):
            await queue.put((event, data))

        def global_callback(job_id: str, event: str, data: dict):
            try:
                asyncio.run_coroutine_threadsafe(_put(event, data), loop)
            except Exception:
                import logging
                logging.getLogger("app").warning("Global SSE on_event failed", exc_info=True)

        pipeline.subscribe_to_all(global_callback)

        try:
            # 发送初始活跃任务列表
            cfg = get_config()
            conn = get_db(cfg.db_path)
            active_rows = list_active_jobs(conn)
            conn.close()
            initial = {"jobs": [Job.from_row(r).to_dict() for r in active_rows]}
            yield f"event: init\ndata: {json.dumps(initial)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            pipeline.unsubscribe_from_all(global_callback)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/api/jobs/{job_id}/log")
async def get_job_log(job_id: str):
    """获取任务日志。"""
    cfg = get_config()
    conn = get_db(cfg.db_path)
    row = get_job(conn, job_id)
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")

    job = Job.from_row(row)
    if not job.storage_dir:
        return {"log": ""}

    log_path = os.path.join(job.storage_dir, "job.log")
    if not os.path.exists(log_path):
        return {"log": ""}

    with open(log_path, "r", encoding="utf-8") as f:
        text = f.read()
    return {"log": text}
