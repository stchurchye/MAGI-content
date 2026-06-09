"""跨任务语义检索：sqlite-vec 向量库 + 可插拔 embedder。

- 存储：sqlite-vec 的 vec0 虚拟表（与现有 SQLite 同栈，零新服务）+ 元数据表。
- embedder 可插拔（EMBEDDING_BACKEND）：
  - "openai"：OpenAI 兼容嵌入端点（如通义 text-embedding，需 EMBEDDING_API_KEY）。
  - "local" ：本地确定性哈希嵌入（无需 key，非语义，仅供联调/测试存取链路）。
  - ""/未设：检索关闭，index/search 变为安全空操作。
- 任务完成后由 pipeline 调 index(job_id, text)；检索走 search(query, k)。
"""
from __future__ import annotations

import hashlib
import logging
import os
import struct
from dataclasses import dataclass
from typing import Optional

_log = logging.getLogger("app.vectorstore")


# ---------------------------------------------------------------------------
# Embedder（可插拔）
# ---------------------------------------------------------------------------

class Embedder:
    dim: int = 1024

    def available(self) -> bool:
        raise NotImplementedError

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class LocalHashEmbedder(Embedder):
    """确定性哈希嵌入：无需 key、可离线验证存取链路。非语义，勿用于生产检索质量。"""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def available(self) -> bool:
        return True

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in (t or "").split():
                h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
                vec[h % self.dim] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out


class OpenAICompatEmbedder(Embedder):
    """OpenAI 兼容嵌入端点（如通义 text-embedding-v3 / OpenAI text-embedding-3-*）。"""

    def __init__(self):
        self.api_key = (os.environ.get("EMBEDDING_API_KEY") or "").strip()
        self.base_url = (os.environ.get("EMBEDDING_BASE_URL")
                         or "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
        self.model = (os.environ.get("EMBEDDING_MODEL") or "text-embedding-v3").strip()
        try:
            self.dim = int(os.environ.get("EMBEDDING_DIM", "1024"))
        except (TypeError, ValueError):
            self.dim = 1024

    def available(self) -> bool:
        return bool(self.api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        resp = client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]


def get_embedder(backend: Optional[str] = None) -> Optional[Embedder]:
    """按 EMBEDDING_BACKEND 取 embedder；未配置返回 None（检索关闭）。"""
    b = (backend if backend is not None else os.environ.get("EMBEDDING_BACKEND", "")).strip().lower()
    if b == "openai":
        emb = OpenAICompatEmbedder()
        if not emb.available():
            _log.warning("EMBEDDING_BACKEND=openai 但未配置 EMBEDDING_API_KEY，检索关闭")
            return None
        return emb
    if b == "local":
        return LocalHashEmbedder()
    return None


# ---------------------------------------------------------------------------
# 向量库
# ---------------------------------------------------------------------------

def _serialize(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


@dataclass
class SearchHit:
    job_id: str
    title: str
    excerpt: str
    distance: float


class VectorStore:
    def __init__(self, db_path: str, embedder: Optional[Embedder]):
        self.db_path = db_path
        self.embedder = embedder

    @property
    def enabled(self) -> bool:
        return self.embedder is not None

    def _table_names(self) -> tuple[str, str]:
        """表名带维度后缀：切换 embedder/维度后用各自的表，避免维度不匹配静默失效。"""
        dim = int(self.embedder.dim)  # 来自本代码，非用户输入，可安全拼入表名
        return f"vec_summaries_d{dim}", f"vec_meta_d{dim}"

    def _connect(self):
        import sqlite3
        import sqlite_vec
        # timeout + WAL：缓解多 worker 线程并发写同一库时的 'database is locked'
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
        except Exception:
            pass
        vt, mt = self._table_names()
        dim = int(self.embedder.dim)
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {vt} USING vec0(embedding float[{dim}])"
        )
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {mt} ("
            "rowid INTEGER PRIMARY KEY, job_id TEXT UNIQUE, title TEXT, excerpt TEXT)"
        )
        return conn

    def index(self, job_id: str, text: str, title: str = "") -> bool:
        """为一个任务的摘要/文本建立向量索引（同 job_id 先删后插，幂等）。"""
        if not self.enabled or not (text or "").strip():
            return False
        try:
            vec = self.embedder.embed([text])[0]
            conn = self._connect()
            vt, mt = self._table_names()
            try:
                row = conn.execute(
                    f"SELECT rowid FROM {mt} WHERE job_id = ?", (job_id,)
                ).fetchone()
                if row is not None:
                    conn.execute(f"DELETE FROM {vt} WHERE rowid = ?", (row[0],))
                    conn.execute(f"DELETE FROM {mt} WHERE rowid = ?", (row[0],))
                cur = conn.execute(
                    f"INSERT INTO {vt}(embedding) VALUES (?)", (_serialize(vec),)
                )
                rowid = cur.lastrowid
                conn.execute(
                    f"INSERT INTO {mt}(rowid, job_id, title, excerpt) VALUES (?,?,?,?)",
                    (rowid, job_id, title or "", (text or "")[:200]),
                )
                conn.commit()
                return True
            finally:
                conn.close()
        except Exception:
            _log.warning("向量索引失败 | job_id=%s", job_id, exc_info=True)
            return False

    def search(self, query: str, k: int = 5) -> list[SearchHit]:
        if not self.enabled or not (query or "").strip():
            return []
        try:
            qvec = self.embedder.embed([query])[0]
            conn = self._connect()
            vt, mt = self._table_names()
            try:
                rows = conn.execute(
                    f"SELECT v.rowid, v.distance, m.job_id, m.title, m.excerpt "
                    f"FROM {vt} v JOIN {mt} m ON m.rowid = v.rowid "
                    f"WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance",
                    (_serialize(qvec), k),
                ).fetchall()
                return [
                    SearchHit(job_id=r[2], title=r[3] or "", excerpt=r[4] or "", distance=r[1])
                    for r in rows
                ]
            finally:
                conn.close()
        except Exception:
            _log.warning("向量检索失败 | query=%s", query[:50], exc_info=True)
            return []


_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    """全局单例。db 路径与 embedder 来自 config / 环境变量。"""
    global _store
    if _store is None:
        from app.config import get_config
        cfg = get_config()
        db_path = os.path.join(cfg.data_dir, "vectors.db")
        _store = VectorStore(db_path, get_embedder())
    return _store
