# -*- coding: utf-8 -*-
"""小墨AI 独立计费账本数据层（TECH-小墨AI计费 v0.2，D4=B）。

分层纪律：本模块是小墨计费表的**唯一 SQL 入口**；金额口径由 service 层按
`webapp/pricing.py` / `webapp/referral.py` 计算后传入，本层只负责「写账 + 扣余额」
在同一事务内原子完成，不重复实现计费公式。

幂等：`UNIQUE(project_id, request_id)` 由数据库兜底；并发同键冲突时回滚整事务并
返回已存在行，绝不重复扣费（AGENTS §11.7「宁可漏扣不重扣」）。
"""
from __future__ import annotations

import time
from decimal import ROUND_HALF_UP, Decimal

import pymysql

from .base import _connect, logger

__all__ = [
    "get_assistant_billing_by_request",
    "charge_assistant_billing",
    "record_pending_assistant_billing",
    "retry_pending_assistant_billing",
    "list_assistant_billing",
    "assistant_billing_stats",
    "mark_assistant_commission_granted",
]

_CENT = Decimal("0.01")


def _money(value) -> Decimal:
    """金额统一到分（Decimal，避免浮点直接比较）。"""
    try:
        return Decimal(str(value or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _row(raw: dict | None) -> dict | None:
    """计费行统一出口：数值转 float/int，便于 HTTP 序列化。"""
    if not raw:
        return None
    return {
        "id": int(raw["id"]),
        "user_id": int(raw["user_id"]),
        "project_id": raw["project_id"],
        "conversation_id": int(raw["conversation_id"]),
        "request_id": raw["request_id"],
        "user_message_id": int(raw.get("user_message_id") or 0),
        "assistant_message_id": int(raw.get("assistant_message_id") or 0),
        "mode": raw.get("mode") or "platform",
        "status": raw.get("status") or "settled",
        "chars": int(raw.get("chars") or 0),
        "unit_price": float(raw.get("unit_price") or 0),
        "due": float(raw.get("due") or 0),
        "charged": float(raw.get("charged") or 0),
        "owe": float(raw.get("owe") or 0),
        "commission_granted": int(raw.get("commission_granted") or 0),
        "created_at": float(raw.get("created_at") or 0),
        "updated_at": float(raw.get("updated_at") or 0),
        "username": raw.get("username") or "",
    }


def get_assistant_billing_by_request(project_id: str, request_id: str) -> dict | None:
    """按 (project_id, request_id) 读计费行（幂等预检入口）。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM assistant_billing WHERE project_id=%s AND request_id=%s",
            (project_id, request_id),
        ).fetchone()
    return _row(row)


def charge_assistant_billing(*, user_id: int, project_id: str, conversation_id: int,
                             request_id: str, user_message_id: int,
                             assistant_message_id: int, mode: str, chars: int,
                             due: float) -> dict:
    """写一行计费并原子扣余额（一次性事务）。

    - `mode != platform` 或 `due <= 0`：只写审计行，不动余额；
    - `mode == platform`：先锁用户行 → 校验唯一键 → 扣到余额为 0 为止 → 写负数流水 → 写账本行；
    - 唯一键冲突（并发同键）：回滚整事务后返回已存在行，不重复扣费。
    """
    now = time.time()
    billable = (mode == "platform" and _money(due) > 0)
    due_m = _money(due) if billable else _money(0)

    with _connect() as conn:
        balance = _money(0)
        if billable:
            urow = conn.execute(
                "SELECT balance FROM users WHERE id=%s FOR UPDATE", (int(user_id),)
            ).fetchone()
            balance = max(_money(0), _money(urow["balance"] if urow else 0))

        existing = conn.execute(
            "SELECT * FROM assistant_billing WHERE project_id=%s AND request_id=%s",
            (project_id, request_id),
        ).fetchone()
        if existing:
            return _row(existing)

        charged = min(balance, due_m) if billable else _money(0)
        charged = max(_money(0), charged)
        owe = max(_money(0), due_m - charged)

        if billable and charged > 0:
            conn.execute("UPDATE users SET balance=%s WHERE id=%s",
                         (float(balance - charged), int(user_id)))
            conn.execute(
                "INSERT INTO recharges (user_id, amount, note, created_at) VALUES (%s,%s,%s,%s)",
                (int(user_id), -float(charged), f"小墨AI 对话消费（{int(chars)} 字）", now),
            )

        params = (
            int(user_id), project_id, int(conversation_id), request_id,
            int(user_message_id or 0), int(assistant_message_id or 0), mode, "settled",
            int(chars or 0), float(_money(3.0)), float(due_m), float(charged), float(owe),
            now, now,
        )
        try:
            cur = conn.execute(
                "INSERT INTO assistant_billing (user_id, project_id, conversation_id, request_id, "
                "user_message_id, assistant_message_id, mode, status, chars, unit_price, due, "
                "charged, owe, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                params,
            )
            billing_id = int(cur.lastrowid)
        except pymysql.err.IntegrityError:
            # 并发同键：回滚本次余额变动，返回已有账本行
            conn.rollback()
            existing = conn.execute(
                "SELECT * FROM assistant_billing WHERE project_id=%s AND request_id=%s",
                (project_id, request_id),
            ).fetchone()
            if existing:
                logger.info("小墨计费并发幂等命中: project=%s request=%s", project_id, request_id)
                return _row(existing)
            raise

    return {
        "id": billing_id, "user_id": int(user_id), "project_id": project_id,
        "conversation_id": int(conversation_id), "request_id": request_id,
        "user_message_id": int(user_message_id or 0),
        "assistant_message_id": int(assistant_message_id or 0),
        "mode": mode, "status": "settled", "chars": int(chars or 0),
        "unit_price": 3.0, "due": float(due_m), "charged": float(charged),
        "owe": float(owe), "commission_granted": 0,
        "created_at": now, "updated_at": now,
    }


def record_pending_assistant_billing(*, user_id: int, project_id: str, conversation_id: int,
                                     request_id: str, user_message_id: int,
                                     assistant_message_id: int, mode: str, chars: int,
                                     due: float) -> dict:
    """结算失败时落待补扣行（D3=3B：返回回答但不漏账，后台可重试）。"""
    now = time.time()
    due_m = _money(due) if mode == "platform" else _money(0)
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO assistant_billing (user_id, project_id, conversation_id, request_id, "
            "user_message_id, assistant_message_id, mode, status, chars, unit_price, due, "
            "charged, owe, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (int(user_id), project_id, int(conversation_id), request_id,
             int(user_message_id or 0), int(assistant_message_id or 0), mode, "pending",
             int(chars or 0), 3.0, float(due_m), 0.0, float(due_m), now, now),
        )
        billing_id = int(cur.lastrowid)
    return {
        "id": billing_id, "user_id": int(user_id), "project_id": project_id,
        "conversation_id": int(conversation_id), "request_id": request_id,
        "user_message_id": int(user_message_id or 0),
        "assistant_message_id": int(assistant_message_id or 0),
        "mode": mode, "status": "pending", "chars": int(chars or 0),
        "unit_price": 3.0, "due": float(due_m), "charged": 0.0,
        "owe": float(due_m), "commission_granted": 0,
        "created_at": now, "updated_at": now,
    }


def retry_pending_assistant_billing(billing_id: int) -> dict:
    """后台补扣：按账本行重新扣费，不重复调用模型。

    锁顺序与 `charge_assistant_billing` 一致（先用户行、再账本行），避免死锁。
    """
    with _connect() as conn:
        base = conn.execute(
            "SELECT * FROM assistant_billing WHERE id=%s", (int(billing_id),)
        ).fetchone()
        if not base:
            raise ValueError("小墨计费记录不存在")
        uid = int(base["user_id"])
        urow = conn.execute(
            "SELECT balance FROM users WHERE id=%s FOR UPDATE", (uid,)
        ).fetchone()
        current = conn.execute(
            "SELECT * FROM assistant_billing WHERE id=%s FOR UPDATE", (int(billing_id),)
        ).fetchone()
        if not current:
            raise ValueError("小墨计费记录不存在")
        if current["status"] == "settled":
            return _row(current)

        mode = current.get("mode") or "platform"
        due_m = _money(current.get("due")) if mode == "platform" else _money(0)
        balance = max(_money(0), _money(urow["balance"] if urow else 0))
        charged = min(balance, due_m)
        owe = max(_money(0), due_m - charged)
        now = time.time()
        if charged > 0:
            conn.execute("UPDATE users SET balance=%s WHERE id=%s",
                         (float(balance - charged), uid))
            conn.execute(
                "INSERT INTO recharges (user_id, amount, note, created_at) VALUES (%s,%s,%s,%s)",
                (uid, -float(charged),
                 f"小墨AI 待补扣（request {current['request_id']}）", now),
            )
        conn.execute(
            "UPDATE assistant_billing SET status='settled', charged=%s, owe=%s, "
            "updated_at=%s WHERE id=%s",
            (float(charged), float(owe), now, int(billing_id)),
        )
        updated = dict(current)
        updated.update({"status": "settled", "charged": float(charged),
                        "owe": float(owe), "updated_at": now})
    logger.info("小墨待补扣完成: billing=%s user=%s charged=%.2f owe=%.2f",
                billing_id, uid, float(charged), float(owe))
    return _row(updated)


def list_assistant_billing(user_id: int = 0, project_id: str = "", status: str = "",
                           limit: int = 100) -> list[dict]:
    """后台小墨消费列表（按时间倒序，可筛选用户/项目/状态）。"""
    sql = ("SELECT b.*, u.username FROM assistant_billing b "
           "LEFT JOIN users u ON u.id=b.user_id WHERE 1=1")
    params: list = []
    if int(user_id or 0) > 0:
        sql += " AND b.user_id=%s"
        params.append(int(user_id))
    if project_id:
        sql += " AND b.project_id=%s"
        params.append(project_id)
    if status:
        sql += " AND b.status=%s"
        params.append(status)
    sql += " ORDER BY b.id DESC LIMIT %s"
    params.append(max(1, min(int(limit or 100), 500)))
    with _connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_row(r) for r in rows]


def assistant_billing_stats() -> dict:
    """后台小墨消费汇总（单列视图，不混入项目生成统计）。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(chars),0) AS chars, COALESCE(SUM(due),0) AS due, "
            "COALESCE(SUM(charged),0) AS charged, COALESCE(SUM(owe),0) AS owe, "
            "COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),0) AS pending "
            "FROM assistant_billing"
        ).fetchone()
    return {
        "total": int(row["total"] or 0),
        "chars": int(row["chars"] or 0),
        "due": float(row["due"] or 0),
        "charged": float(row["charged"] or 0),
        "owe": float(row["owe"] or 0),
        "pending": int(row["pending"] or 0),
    }


def mark_assistant_commission_granted(billing_id: int) -> None:
    """返佣发放成功后置位，防补偿任务重复发放。"""
    with _connect() as conn:
        conn.execute(
            "UPDATE assistant_billing SET commission_granted=1, updated_at=%s WHERE id=%s",
            (time.time(), int(billing_id)),
        )
