"""SQLite 数据库层：schema 初始化与全部 CRUD 操作。"""
import sqlite3
from typing import Optional

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    url             TEXT NOT NULL,
    platform        TEXT,
    downloader      TEXT,
    title           TEXT,
    media_type      TEXT DEFAULT 'video',
    duration_sec    REAL,
    status          TEXT NOT NULL DEFAULT 'pending',
    current_stage   TEXT,
    storage_dir     TEXT,
    video_path      TEXT,
    audio_path      TEXT,
    images_dir      TEXT,
    danmaku_path    TEXT,
    transcript_path TEXT,
    summary_path    TEXT,
    transcript_text TEXT,
    summary_text    TEXT,
    error_message   TEXT,
    error_stage     TEXT,
    retry_count     INTEGER DEFAULT 0,
    progress_pct    INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_platform ON jobs(platform);
CREATE INDEX IF NOT EXISTS idx_jobs_archived ON jobs(is_archived);
"""

ACTIVE_STATUSES = (
    "pending",
    "downloading",
    "extracting",
    "transcribing",
    "summarizing",
)


def migrate_db(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "is_archived" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN is_archived INTEGER DEFAULT 0")


def get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    migrate_db(conn)
    return conn


# ---------- 写入 ----------

def insert_job(conn: sqlite3.Connection, job_id: str, url: str) -> None:
    conn.execute(
        "INSERT INTO jobs (id, url) VALUES (?, ?)",
        (job_id, url),
    )


def update_job_status(
    conn: sqlite3.Connection,
    job_id: str,
    status: str,
    current_stage: str | None = None,
    progress_pct: int | None = None,
    error_message: str | None = None,
    error_stage: str | None = None,
) -> None:
    sets = ["status = ?", "updated_at = datetime('now')"]
    params: list = [status]
    if current_stage is not None:
        sets.append("current_stage = ?")
        params.append(current_stage)
    if progress_pct is not None:
        sets.append("progress_pct = ?")
        params.append(progress_pct)
    if error_message is not None:
        sets.append("error_message = ?")
        params.append(error_message)
        sets.append("error_stage = ?")
        params.append(error_stage or "")
    params.append(job_id)
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)


ALLOWED_JOB_FIELDS = {
    "url", "platform", "downloader", "title", "media_type", "duration_sec",
    "status", "current_stage", "storage_dir", "video_path", "audio_path",
    "images_dir", "danmaku_path", "transcript_path", "summary_path",
    "transcript_text", "summary_text", "error_message", "error_stage",
    "retry_count", "progress_pct", "is_archived",
}


def update_job_fields(conn: sqlite3.Connection, job_id: str, **fields) -> None:
    if not fields:
        return
    invalid = set(fields.keys()) - ALLOWED_JOB_FIELDS
    if invalid:
        raise ValueError(f"Invalid field names: {invalid}")
    sets = ["updated_at = datetime('now')"]
    params: list = []
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        params.append(v)
    params.append(job_id)
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)


def increment_retry(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(
        "UPDATE jobs SET retry_count = retry_count + 1, "
        "status = 'pending', error_message = NULL, error_stage = NULL, "
        "updated_at = datetime('now') WHERE id = ?",
        (job_id,),
    )


# ---------- 读取 ----------

def get_job(conn: sqlite3.Connection, job_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def _job_filter_where(
    *,
    status: str | None = None,
    is_archived: bool | None = None,
    platform: str | None = None,
    exclude_statuses: tuple[str, ...] | None = None,
) -> tuple[str, list]:
    conditions: list[str] = []
    params: list = []
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if exclude_statuses:
        placeholders = ",".join("?" * len(exclude_statuses))
        conditions.append(f"status NOT IN ({placeholders})")
        params.extend(exclude_statuses)
    if is_archived is not None:
        conditions.append("is_archived = ?")
        params.append(1 if is_archived else 0)
    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return where, params


def list_jobs(
    conn: sqlite3.Connection,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    is_archived: bool | None = None,
    platform: str | None = None,
    exclude_statuses: tuple[str, ...] | None = None,
) -> list[dict]:
    where, params = _job_filter_where(
        status=status,
        is_archived=is_archived,
        platform=platform,
        exclude_statuses=exclude_statuses,
    )
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT * FROM jobs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def count_jobs(
    conn: sqlite3.Connection,
    status: str | None = None,
    is_archived: bool | None = None,
    platform: str | None = None,
    exclude_statuses: tuple[str, ...] | None = None,
) -> int:
    where, params = _job_filter_where(
        status=status,
        is_archived=is_archived,
        platform=platform,
        exclude_statuses=exclude_statuses,
    )
    row = conn.execute(f"SELECT COUNT(*) FROM jobs{where}", params).fetchone()
    return row[0] if row else 0


def search_jobs(
    conn: sqlite3.Connection,
    query: str,
    status: str | None = None,
    is_archived: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    platform: str | None = None,
    exclude_statuses: tuple[str, ...] | None = None,
) -> list[dict]:
    conditions = ["(title LIKE ? OR url LIKE ?)"]
    params: list = [f"%{query}%", f"%{query}%"]
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if exclude_statuses:
        placeholders = ",".join("?" * len(exclude_statuses))
        conditions.append(f"status NOT IN ({placeholders})")
        params.extend(exclude_statuses)
    if is_archived is not None:
        conditions.append("is_archived = ?")
        params.append(1 if is_archived else 0)
    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    where = f" WHERE {' AND '.join(conditions)}"
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT * FROM jobs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def count_search_jobs(
    conn: sqlite3.Connection,
    query: str,
    status: str | None = None,
    is_archived: bool | None = None,
    platform: str | None = None,
    exclude_statuses: tuple[str, ...] | None = None,
) -> int:
    conditions = ["(title LIKE ? OR url LIKE ?)"]
    params: list = [f"%{query}%", f"%{query}%"]
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if exclude_statuses:
        placeholders = ",".join("?" * len(exclude_statuses))
        conditions.append(f"status NOT IN ({placeholders})")
        params.extend(exclude_statuses)
    if is_archived is not None:
        conditions.append("is_archived = ?")
        params.append(1 if is_archived else 0)
    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    where = f" WHERE {' AND '.join(conditions)}"
    row = conn.execute(f"SELECT COUNT(*) FROM jobs{where}", params).fetchone()
    return row[0] if row else 0


def archive_job(conn: sqlite3.Connection, job_id: str, archived: bool = True) -> None:
    conn.execute(
        "UPDATE jobs SET is_archived = ?, updated_at = datetime('now') WHERE id = ?",
        (1 if archived else 0, job_id),
    )


def list_active_jobs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status IN ('pending','downloading','extracting','transcribing','summarizing') "
        "ORDER BY created_at ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def count_stuck_jobs(conn: sqlite3.Connection, stale_minutes: int) -> int:
    """进行中但长时间未更新的任务（服务重启或下载挂起）。"""
    row = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status IN "
        "('pending','downloading','extracting','transcribing','summarizing') "
        "AND updated_at < datetime('now', ?)",
        (f"-{stale_minutes} minutes",),
    ).fetchone()
    return row[0] if row else 0


# ---------- 删除 ----------

def delete_job(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
