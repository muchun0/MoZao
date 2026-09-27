# -*- coding: utf-8 -*-
"""项目归属与退款域数据访问。"""
from __future__ import annotations

import time
from typing import Optional

from .base import _connect

__all__ = [
    "all_projects_with_owner", "claim_project", "project_discount", "project_price",
    "update_project_price", "find_commission_by_project", "project_owner",
    "list_user_projects", "all_projects", "delete_project_owner",
    "refundable_amount", "create_refund_request", "list_refund_requests", "handle_refund",
]


def all_projects_with_owner() -> list[dict]:
    """所有项目 + 归属用户名（管理页项目总览用）。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT po.project_id, po.user_id, u.username, po.created_at "
            "FROM project_owner po LEFT JOIN users u ON u.id=po.user_id "
            "ORDER BY po.created_at DESC").fetchall()
    return [dict(r) for r in rows]


# ---------- 项目归属 ----------
def claim_project(project_id: str, user_id: int, price: float = 0.0, discount: float = 1.0) -> None:
    """登记项目归属。price=创建时预估/结算后实扣；discount=创建时代理折扣快照（结算用）。"""
    with _connect() as conn:
        conn.execute(
            "INSERT IGNORE INTO project_owner (project_id, user_id, price, discount, created_at) VALUES (%s,%s,%s,%s,%s)",
            (project_id, user_id, price, discount, time.time()))


def project_discount(project_id: str) -> float:
    """项目创建时的代理折扣快照（老项目/无记录返回 1.0）。"""
    with _connect() as conn:
        row = conn.execute("SELECT discount FROM project_owner WHERE project_id=%s", (project_id,)).fetchone()
    return float(row["discount"]) if row and row["discount"] is not None else 1.0


def project_price(project_id: str) -> float:
    """项目创建时实扣金额（含代理折扣）。老项目无记录返回 0（不可退费）。"""
    with _connect() as conn:
        row = conn.execute("SELECT price FROM project_owner WHERE project_id=%s", (project_id,)).fetchone()
    return float(row["price"]) if row and row["price"] is not None else 0.0


def update_project_price(project_id: str, price: float) -> None:
    """更新项目实付金额（生成完成后按实际 AI 字数结算时调用）。"""
    with _connect() as conn:
        conn.execute("UPDATE project_owner SET price=%s WHERE project_id=%s", (price, project_id))


def find_commission_by_project(project_id: str) -> Optional[dict]:
    """查触发该项目的消费返佣记录（用于退费冲正）。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM commission_log WHERE project_id=%s AND kind='consume' "
            "ORDER BY id LIMIT 1",
            (project_id,),
        ).fetchone()
    return dict(row) if row else None


def project_owner(project_id: str) -> Optional[int]:
    with _connect() as conn:
        row = conn.execute("SELECT user_id FROM project_owner WHERE project_id=%s", (project_id,)).fetchone()
    return row["user_id"] if row else None


def list_user_projects(user_id: int) -> set[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT project_id FROM project_owner WHERE user_id=%s", (user_id,)).fetchall()
    return {r["project_id"] for r in rows}


def all_projects() -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute("SELECT project_id, user_id FROM project_owner").fetchall()
    return {r["project_id"]: r["user_id"] for r in rows}


def delete_project_owner(project_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM project_owner WHERE project_id=%s", (project_id,))


# ---------- 退款申请 ----------
def refundable_amount(user_id: int) -> float:
    """可退金额 = 累计充值（管理员手动充值） - 已批准退款。

    注册赠金、代理返佣属于平台赠送/奖励，不可退，不计入可退金额。
    """
    with _connect() as conn:
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM recharges "
            "WHERE user_id=%s AND amount>0 AND note LIKE '管理员手动充值%'",
            (user_id,)).fetchone()["s"]
        refunded = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM refund_requests "
            "WHERE user_id=%s AND status='approved'",
            (user_id,)).fetchone()["s"]
    return round(max(float(paid) - float(refunded), 0.0), 2)


def create_refund_request(user_id: int, amount: float, reason: str) -> tuple[bool, str]:
    """提交退款申请。返回 (成功?, 信息)。防重复：有 pending 申请时不允许再提。"""
    if amount <= 0:
        return False, "退款金额必须大于 0"
    with _connect() as conn:
        pending = conn.execute(
            "SELECT id FROM refund_requests WHERE user_id=%s AND status='pending'",
            (user_id,)).fetchone()
        if pending:
            return False, "你已有一笔待处理退款申请，请等待站长处理"
        refundable = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM recharges "
            "WHERE user_id=%s AND amount>0 AND note LIKE '管理员手动充值%'",
            (user_id,)).fetchone()["s"]
        refunded = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM refund_requests "
            "WHERE user_id=%s AND status='approved'",
            (user_id,)).fetchone()["s"]
        if amount > round(float(refundable) - float(refunded), 2):
            return False, f"可退金额不足（当前可退 {round(float(refundable) - float(refunded), 2):.2f} 元）"
        conn.execute(
            "INSERT INTO refund_requests (user_id, amount, reason, status, created_at) VALUES (%s,%s,%s,%s,%s)",
            (user_id, amount, (reason or "").strip()[:200], "pending", time.time()))
    return True, "退款申请已提交，站长处理后到账"


def list_refund_requests(user_id: Optional[int] = None, status: Optional[str] = None) -> list[dict]:
    with _connect() as conn:
        sql = "SELECT * FROM refund_requests WHERE 1=1"
        params: list = []
        if user_id:
            sql += " AND user_id=%s"
            params.append(user_id)
        if status:
            sql += " AND status=%s"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT 200"
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def handle_refund(request_id: int, action: str, admin_note: str = "") -> tuple[bool, str]:
    """站长处理退款。action=approve：线下已转账，扣用户余额并标记完成；
    action=reject：拒绝退款并备注原因。"""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM refund_requests WHERE id=%s", (request_id,)).fetchone()
        if not row:
            return False, "退款申请不存在"
        if row["status"] != "pending":
            return False, "该申请已处理，请勿重复操作"
        if action == "approve":
            # 从用户余额扣除（余额可能不足申请额，扣到 0 为止）
            bal_row = conn.execute("SELECT balance FROM users WHERE id=%s", (row["user_id"],)).fetchone()
            balance = float(bal_row["balance"]) if bal_row else 0.0
            deduct = round(min(float(row["amount"]), balance), 2)
            conn.execute("UPDATE users SET balance = balance - %s WHERE id=%s", (deduct, row["user_id"]))
            conn.execute("INSERT INTO recharges (user_id, amount, note, created_at) VALUES (%s,%s,%s,%s)",
                         (row["user_id"], -deduct, f"退款给用户（申请#{request_id}）", time.time()))
            conn.execute(
                "UPDATE refund_requests SET status='approved', admin_note=%s, handled_at=%s WHERE id=%s",
                ((admin_note or "").strip()[:200], time.time(), request_id))
            return True, f"已确认退款 {row['amount']:.2f} 元（余额扣除 {deduct:.2f} 元）"
        elif action == "reject":
            conn.execute(
                "UPDATE refund_requests SET status='rejected', admin_note=%s, handled_at=%s WHERE id=%s",
                ((admin_note or "").strip()[:200], time.time(), request_id))
            return True, "已拒绝该退款申请"
        return False, "未知操作"
