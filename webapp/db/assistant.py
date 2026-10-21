# -*- coding: utf-8 -*-
"""
小墨AI 会话数据层（REQ-小墨AI 一期）：conversations / messages 全部 SQL。

分层纪律（AGENTS.md §11.2）：本模块是会话数据的**唯一 SQL 入口**；
业务规则、提示词组装、LLM 调用一律不在此处（见 webapp/assistant/）。
归属校验：所有按会话操作的查询都带 project_id 条件，防跨项目越权。
"""
from __future__ import annotations

import json
import time

from .base import _connect, logger

__all__ = [
    "create_conversation", "list_conversations", "get_conversation",
    "delete_conversation", "add_message", "list_messages", "recent_messages",
]


def _parse_extra(raw) -> dict:
    """messages.extra 为 JSON 列：统一转 dict（损坏时降级为空 dict，留痕）。"""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        logger.warning("messages.extra JSON 解析失败，按空处理")
        return {}


def _conv_row(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "project_id": row["project_id"],
        "user_id": int(row["user_id"]),
        "title": row.get("title") or "",
        "mode": row.get("mode") or "chapter_eval",
        "created_at": float(row.get("created_at") or 0),
        "updated_at": float(row.get("updated_at") or 0),
    }


def _msg_row(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "conversation_id": int(row["conversation_id"]),
        "role": row["role"],
        "content": row.get("content") or "",
        "extra": _parse_extra(row.get("extra")),
        "created_at": float(row.get("created_at") or 0),
    }


def create_conversation(project_id: str, user_id: int, title: str = "",
                        mode: str = "chapter_eval") -> int:
    """创建会话，返回会话 id。"""
    now = time.time()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (project_id, user_id, title, mode, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (project_id, int(user_id), (title or "")[:200], mode, now, now),
        )
        return int(cur.lastrowid)


def list_conversations(project_id: str, user_id: int, limit: int = 50) -> list[dict]:
    """会话列表（最近更新在前），附最近消息摘要与消息数。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT c.*, "
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS msg_count, "
            "(SELECT m2.content FROM messages m2 WHERE m2.conversation_id=c.id "
            " ORDER BY m2.id DESC LIMIT 1) AS last_message "
            "FROM conversations c WHERE c.project_id=%s AND c.user_id=%s "
            "ORDER BY c.updated_at DESC, c.id DESC LIMIT %s",
            (project_id, int(user_id), int(limit)),
        ).fetchall()
    items = []
    for r in rows:
        item = _conv_row(r)
        item["message_count"] = int(r.get("msg_count") or 0)
        item["last_message"] = (r.get("last_message") or "")[:120]
        items.append(item)
    return items


def get_conversation(cid: int, project_id: str) -> dict | None:
    """按 id + project_id 归属校验读取会话。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id=%s AND project_id=%s",
            (int(cid), project_id),
        ).fetchone()
    return _conv_row(row) if row else None


def delete_conversation(cid: int, project_id: str) -> bool:
    """删除会话并级联删除消息（同一事务，返回是否删到会话）。"""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM conversations WHERE id=%s AND project_id=%s",
            (int(cid), project_id),
        )
        if cur.rowcount <= 0:
            return False
        conn.execute("DELETE FROM messages WHERE conversation_id=%s", (int(cid),))
    return True


def add_message(cid: int, role: str, content: str, extra: dict | None = None) -> int:
    """落一条消息并刷新会话 updated_at，返回消息 id。"""
    now = time.time()
    payload = json.dumps(extra, ensure_ascii=False) if extra else None
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, extra, created_at) "
            "VALUES (%s,%s,%s,%s,%s)",
            (int(cid), role, content or "", payload, now),
        )
        conn.execute("UPDATE conversations SET updated_at=%s WHERE id=%s", (now, int(cid)))
        return int(cur.lastrowid)


def list_messages(cid: int, limit: int = 50, before_id: int = 0) -> list[dict]:
    """按 id 升序返回消息；传 before_id 时取该 id 之前的一页（向上翻页）。"""
    limit = max(1, min(int(limit), 200))
    with _connect() as conn:
        if int(before_id or 0) > 0:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id=%s AND id<%s "
                "ORDER BY id DESC LIMIT %s",
                (int(cid), int(before_id), limit),
            ).fetchall()
            rows = list(reversed(rows))
        else:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id=%s ORDER BY id DESC LIMIT %s",
                (int(cid), limit),
            ).fetchall()
            rows = list(reversed(rows))
    return [_msg_row(r) for r in rows]


def recent_messages(cid: int, limit: int = 12) -> list[dict]:
    """组装上下文用：最近 N 条消息（升序）。"""
    return list_messages(cid, limit=limit)
