# -*- coding: utf-8 -*-
"""爆款拆书库域数据访问：调研沉淀、手动拆解、种子条目、浏览查询。"""
from __future__ import annotations

import time
from typing import Optional

from .base import _connect

__all__ = [
    "insert_manual_teardown", "upsert_research_teardown", "seed_teardown",
    "list_teardowns", "list_teardown_genres", "get_teardown", "delete_teardown",
]


def _ensure_teardown_user_id() -> None:
    """幂等迁移：book_teardown 增加 user_id（记录手动拆解投稿人，历史数据默认 0）。"""
    try:
        with _connect() as conn:
            row = conn.execute("SHOW COLUMNS FROM book_teardown LIKE 'user_id'").fetchone()
            if row is None:
                conn.execute("ALTER TABLE book_teardown ADD COLUMN user_id INT NOT NULL DEFAULT 0")
    except Exception:
        # 表尚未创建/连接失败时静默（init_db 已建表；此处仅兜底）
        pass


def insert_manual_teardown(genre: str, title: str, summary: str, content: str,
                           user_id: int) -> int:
    """用户手动拆解入库（source_type=manual，无关联项目）。返回条目 id。"""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO book_teardown (genre,title,summary,content,source_project,source_type,view_count,status,created_at,updated_at,user_id) "
            "VALUES (%s,%s,%s,%s,'','manual',0,1,%s,%s,%s)",
            (genre, title, summary, content, time.time(), time.time(), user_id))
        return cur.lastrowid


def upsert_research_teardown(genre: str, title: str, summary: str, content: str,
                             source_project: str) -> int:
    """调研成果沉淀：同一项目已有条目则更新（幂等）；否则按题材合并更新或新建。返回条目 id。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM book_teardown WHERE source_type='research' AND source_project=%s LIMIT 1",
            (source_project,)).fetchone()
        if row:
            conn.execute(
                "UPDATE book_teardown SET genre=%s,title=%s,summary=%s,content=%s,updated_at=%s WHERE id=%s",
                (genre, title, summary, content, time.time(), row["id"]))
            return row["id"]
        row = conn.execute(
            "SELECT id FROM book_teardown WHERE source_type='research' AND genre=%s LIMIT 1",
            (genre,)).fetchone()
        if row:
            conn.execute(
                "UPDATE book_teardown SET title=%s,summary=%s,content=%s,source_project=%s,updated_at=%s WHERE id=%s",
                (title, summary, content, source_project, time.time(), row["id"]))
            return row["id"]
        cur = conn.execute(
            "INSERT INTO book_teardown (genre,title,summary,content,source_project,source_type,view_count,status,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,%s,'research',0,1,%s,%s)",
            (genre, title, summary, content, source_project, time.time(), time.time()))
        return cur.lastrowid


def seed_teardown(genre: str, title: str, summary: str, content: str) -> Optional[int]:
    """导入种子拆书条目（source_type=seed，按 genre 幂等）：已存在返回 None，否则新建并返回 id。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM book_teardown WHERE source_type='seed' AND genre=%s LIMIT 1",
            (genre,)).fetchone()
        if row:
            return None
        cur = conn.execute(
            "INSERT INTO book_teardown (genre,title,summary,content,source_project,source_type,view_count,status,created_at,updated_at) "
            "VALUES (%s,%s,%s,%s,'','seed',0,1,%s,%s)",
            (genre, title, summary, content, time.time(), time.time()))
        return cur.lastrowid


def list_teardowns(genre: str = "", q: str = "", page: int = 1, page_size: int = 20) -> dict:
    """拆书库列表（不含 content 全文），返回 {"total", "items"}。"""
    where = ["status=1"]
    args: list = []
    if genre:
        where.append("genre=%s")
        args.append(genre)
    if q:
        where.append("(title LIKE %s OR summary LIKE %s)")
        args += [f"%{q}%", f"%{q}%"]
    cond = " AND ".join(where)
    with _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM book_teardown WHERE {cond}", args).fetchone()["c"]
        rows = conn.execute(
            f"SELECT id,genre,title,summary,source_type,view_count,created_at,updated_at "
            f"FROM book_teardown WHERE {cond} ORDER BY updated_at DESC LIMIT %s OFFSET %s",
            args + [page_size, (page - 1) * page_size]).fetchall()
        return {"total": total, "items": [dict(r) for r in rows]}


def list_teardown_genres() -> list[dict]:
    """题材聚合：返回 [{genre, count}]，按 count 降序（筛选 chips 用）。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT genre, COUNT(*) AS count FROM book_teardown WHERE status=1 "
            "GROUP BY genre ORDER BY count DESC, genre ASC").fetchall()
        return [dict(r) for r in rows]


def get_teardown(td_id: int, inc_view: bool = False) -> Optional[dict]:
    """拆书条目详情；inc_view=True 时浏览数 +1（列表接口不做自增）。"""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM book_teardown WHERE id=%s AND status=1", (td_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        if inc_view:
            conn.execute("UPDATE book_teardown SET view_count=view_count+1 WHERE id=%s", (td_id,))
            d["view_count"] = (d["view_count"] or 0) + 1
        return d


def delete_teardown(td_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM book_teardown WHERE id=%s", (td_id,))
        return cur.rowcount > 0
