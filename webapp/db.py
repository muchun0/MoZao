# -*- coding: utf-8 -*-
"""
多用户数据层（SQLite）：用户、会话、余额、项目归属、充值记录、代理返佣。

为什么用 SQLite：单机部署、零配置、无额外服务，够支撑内测期的多用户并发。
表结构：
    users          用户（id, username, pass_hash, email, balance, is_admin, created_at,
                         is_agent, agent_discount, invite_code, referrer_id）
    sessions       登录会话（token, user_id, created_at）
    project_owner  项目归属（project_id -> user_id）
    recharges      余额流水（充值/扣费/返佣都记录在这里，amount 可正可负）
    commission_log 返佣明细（谁给谁返了多少、哪笔消费触发）
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional

# 数据库路径：默认在 webapp/ 包目录；Docker 部署可用环境变量 NOVEL_DB_PATH 指向卷内路径
DB_PATH = Path(os.environ.get("NOVEL_DB_PATH")
               or Path(__file__).resolve().parent / "novel_web.db")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                pass_hash TEXT NOT NULL,
                balance REAL NOT NULL DEFAULT 0,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_owner (
                project_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                price REAL NOT NULL DEFAULT 0,   -- 创建时实扣金额（代理按折扣价），用于项目退费
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recharges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                note TEXT DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS commission_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,      -- 获佣的上级（代理）
                buyer_id INTEGER NOT NULL,      -- 消费/充值的下级
                amount REAL NOT NULL,           -- 返佣金额
                kind TEXT NOT NULL,             -- consume 消费返佣 / recharge 充值返佣 / register 拉新奖励
                project_id TEXT DEFAULT '',     -- 触发返佣的项目（消费返佣时）
                note TEXT DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS refund_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,       -- 申请人
                amount REAL NOT NULL,           -- 申请退款金额
                reason TEXT DEFAULT '',         -- 退款原因
                status TEXT NOT NULL DEFAULT 'pending',  -- pending 待处理 / approved 已退 / rejected 已拒绝
                admin_note TEXT DEFAULT '',     -- 站长处理备注
                created_at REAL NOT NULL,
                handled_at REAL
            );
            """
        )
        # 代理相关列迁移（老库升级用）
        cols = _column_names(conn, "users")
        if "email" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        if "is_agent" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_agent INTEGER NOT NULL DEFAULT 0")
        if "agent_discount" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN agent_discount REAL NOT NULL DEFAULT 1.0")
        if "invite_code" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN invite_code TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_invite ON users(invite_code)")
        if "referrer_id" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER")
        # 项目归属表补 price 列（老库升级用，退费功能）
        pcols = _column_names(conn, "project_owner")
        if "price" not in pcols:
            conn.execute("ALTER TABLE project_owner ADD COLUMN price REAL NOT NULL DEFAULT 0")
        # 老用户补邀请码（无码则按 id 生成 INV+id）
        rows = conn.execute("SELECT id FROM users WHERE invite_code IS NULL OR invite_code=''").fetchall()
        for r in rows:
            code = _make_invite_code(r["id"])
            conn.execute("UPDATE users SET invite_code=? WHERE id=?", (code, r["id"]))


def _make_invite_code(user_id: int) -> str:
    """生成邀请码：INV + 用户id（可读、可回溯）。"""
    return f"INV{user_id}"


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


# ---------- 用户 ----------
def create_user(username: str, password: str, is_admin: bool = False,
                invite_code: str = "", email: str = "") -> tuple[bool, str]:
    """注册。返回 (成功?, 信息)。用户名或邮箱已存在返回 (False, 原因)。

    email 必填：格式校验 + 全站唯一（不区分大小写，统一存小写）。
    历史老用户 email 为空（NULL）不受影响。
    invite_code 非空时立即尝试绑定上级；绑定失败只记录在 info 里，不阻断注册。
    """
    username = username.strip()
    email = (email or "").strip().lower()
    if not username or not password:
        return False, "用户名和密码不能为空"
    if len(username) > 20:
        return False, "用户名不能超过 20 个字符"
    if not email:
        return False, "邮箱不能为空"
    if not _valid_email(email):
        return False, "邮箱格式不正确"
    salt = secrets.token_hex(8)
    with _connect() as conn:
        # 先查邮箱占用，给出友好错误（唯一索引兜底）
        ex = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if ex:
            return False, "该邮箱已被注册"
        try:
            cur = conn.execute(
                "INSERT INTO users (username, pass_hash, email, balance, is_admin, created_at) VALUES (?,?,?,?,?,?)",
                (username, _hash_password(password, salt) + ":" + salt, email, 0, int(is_admin), time.time()),
            )
        except sqlite3.IntegrityError:
            return False, "用户名已被注册"
        new_id = cur.lastrowid
        code = _make_invite_code(new_id)
        conn.execute("UPDATE users SET invite_code=? WHERE id=?", (code, new_id))
        if invite_code and invite_code.strip():
            ok, why = _bind_referrer_inner(conn, new_id, invite_code.strip())
            if not ok:
                return True, f"注册成功（邀请码未生效：{why}）"
    return True, "ok"


# ---------- 代理 / 邀请 ----------
def get_invite_code(user_id: int) -> str:
    """取用户邀请码（没有则补生成）。"""
    with _connect() as conn:
        row = conn.execute("SELECT invite_code FROM users WHERE id=?", (user_id,)).fetchone()
        if row and row["invite_code"]:
            return row["invite_code"]
        code = _make_invite_code(user_id)
        conn.execute("UPDATE users SET invite_code=? WHERE id=?", (code, user_id))
        return code


def set_agent(user_id: int, discount: float = 0.8) -> None:
    """把用户设为代理并设定折扣（0 < discount <= 1）。"""
    discount = max(0.01, min(float(discount), 1.0))
    with _connect() as conn:
        conn.execute("UPDATE users SET is_agent=1, agent_discount=? WHERE id=?", (discount, user_id))


def unset_agent(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET is_agent=0, agent_discount=1.0 WHERE id=?", (user_id,))


def bind_referrer(user_id: int, invite_code: str) -> tuple[bool, str]:
    """用户主动补绑上级（仅当尚无上级时）。invite_code 是上级的邀请码。"""
    with _connect() as conn:
        return _bind_referrer_inner(conn, user_id, invite_code)


def _bind_referrer_inner(conn: sqlite3.Connection, user_id: int, invite_code: str) -> tuple[bool, str]:
    """绑定核心逻辑（事务内）。"""
    invite_code = (invite_code or "").strip()
    if not invite_code:
        return False, "邀请码为空"
    row = conn.execute("SELECT id, username FROM users WHERE invite_code=?", (invite_code,)).fetchone()
    if not row:
        return False, "邀请码不存在"
    referrer_id = row["id"]
    if referrer_id == user_id:
        return False, "不能填自己的邀请码"
    me = conn.execute("SELECT referrer_id FROM users WHERE id=?", (user_id,)).fetchone()
    if me is None:
        return False, "用户不存在"
    if me["referrer_id"] is not None:
        return False, "你已经绑定了上级，无法更换"
    conn.execute("UPDATE users SET referrer_id=? WHERE id=?", (referrer_id, user_id))
    return True, "ok"


def get_referrer(user_id: int) -> Optional[dict]:
    """取上级用户信息（未绑定返回 None）。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT u.id, u.username, u.is_agent, u.agent_discount FROM users u "
            "JOIN users me ON me.referrer_id = u.id WHERE me.id=?",
            (user_id,)).fetchone()
    return dict(row) if row else None


def list_downline(agent_id: int, limit: int = 100) -> list[dict]:
    """取下级用户列表（按加入时间倒序）。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, username, balance, created_at FROM users WHERE referrer_id=? ORDER BY id DESC LIMIT ?",
            (agent_id, limit)).fetchall()
    return [dict(r) for r in rows]


def count_downline(agent_id: int) -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM users WHERE referrer_id=?", (agent_id,)).fetchone()
    return row["c"] if row else 0


def pay_commission(agent_id: int, buyer_id: int, amount: float, kind: str,
                   project_id: str = "", note: str = "") -> bool:
    """给代理返佣：入余额 + 写余额流水 + 写返佣明细。返回是否成功。"""
    if amount <= 0:
        return False
    with _connect() as conn:
        conn.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, agent_id))
        conn.execute("INSERT INTO recharges (user_id, amount, note, created_at) VALUES (?,?,?,?)",
                     (agent_id, amount, note or f"返佣（{kind}）", time.time()))
        conn.execute(
            "INSERT INTO commission_log (agent_id, buyer_id, amount, kind, project_id, note, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (agent_id, buyer_id, amount, kind, project_id, note, time.time()))
    return True


def list_commissions(user_id: int, limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM commission_log WHERE agent_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


def sum_commission(user_id: int) -> float:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM commission_log WHERE agent_id=?", (user_id,)).fetchone()
    return row["s"] if row else 0.0


def list_agents() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, username, balance, agent_discount, created_at FROM users WHERE is_agent=1 ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def authenticate(identifier: str, password: str) -> Optional[int]:
    """校验登录，成功返回 user_id。identifier 支持用户名或邮箱（邮箱不区分大小写）。"""
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    with _connect() as conn:
        if "@" in identifier:
            row = conn.execute("SELECT id, pass_hash FROM users WHERE email=?", (identifier.lower(),)).fetchone()
        else:
            row = conn.execute("SELECT id, pass_hash FROM users WHERE username=?", (identifier,)).fetchone()
    if not row:
        return None
    pass_hash, salt = (row["pass_hash"] or "").rsplit(":", 1)
    if hmac.compare_digest(pass_hash, _hash_password(password, salt)):
        return row["id"]
    return None


def get_user(user_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_name(username: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", ((email or "").strip().lower(),)).fetchone()
    return dict(row) if row else None


def update_password(user_id: int, new_password: str) -> None:
    """重置密码（忘记密码用）。"""
    if not new_password or len(new_password) < 4:
        return
    salt = secrets.token_hex(8)
    with _connect() as conn:
        conn.execute("UPDATE users SET pass_hash=? WHERE id=?",
                     (_hash_password(new_password, salt) + ":" + salt, user_id))


def delete_user_sessions(user_id: int) -> None:
    """删除某用户全部会话（重置密码后强制重新登录）。"""
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def change_balance(user_id: int, delta: float, note: str = "") -> float:
    """给用户加/扣余额（delta 可为负），返回新余额。"""
    with _connect() as conn:
        conn.execute("UPDATE users SET balance = balance + ? WHERE id=?", (delta, user_id))
        conn.execute("INSERT INTO recharges (user_id, amount, note, created_at) VALUES (?,?,?,?)",
                     (user_id, delta, note, time.time()))
        row = conn.execute("SELECT balance FROM users WHERE id=?", (user_id,)).fetchone()
    return row["balance"] if row else 0.0


def list_recharges(user_id: Optional[int] = None, limit: int = 50) -> list[dict]:
    with _connect() as conn:
        if user_id:
            rows = conn.execute(
                "SELECT * FROM recharges WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM recharges ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------- 管理统计 ----------
def admin_stats() -> dict:
    """经营统计：用户数 / 代理数 / 充值总额 / 消费总额 / 返佣总额 / 注册赠金。"""
    with _connect() as conn:
        u = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        a = conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_agent=1").fetchone()["c"]
        r = conn.execute(
            "SELECT "
            "COALESCE(SUM(CASE WHEN amount>0 AND note LIKE '管理员手动充值%' THEN amount ELSE 0 END),0) AS recharge, "
            "COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0) AS consume, "
            "COALESCE(SUM(CASE WHEN note='注册赠送' THEN amount ELSE 0 END),0) AS bonus "
            "FROM recharges").fetchone()
        c = conn.execute("SELECT COALESCE(SUM(amount),0) AS s FROM commission_log").fetchone()["s"]
        p = conn.execute("SELECT COUNT(*) AS c FROM project_owner").fetchone()["c"]
    return {
        "users": u, "agents": a, "projects": p,
        "total_recharge": r["recharge"],
        "total_consume": r["consume"],
        "total_bonus": r["bonus"],
        "total_commission": c,
    }


def list_users_admin() -> list[dict]:
    """管理用用户列表（含代理/邀请/下级数/累计返佣）。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT u.id, u.username, u.email, u.balance, u.is_admin, u.is_agent, u.agent_discount, "
            "u.invite_code, u.created_at, "
            "(SELECT COUNT(*) FROM users d WHERE d.referrer_id=u.id) AS downline_count, "
            "(SELECT COALESCE(SUM(amount),0) FROM commission_log cl WHERE cl.agent_id=u.id) AS total_commission "
            "FROM users u ORDER BY u.id").fetchall()
    return [dict(r) for r in rows]


def all_projects_with_owner() -> list[dict]:
    """所有项目 + 归属用户名（管理页项目总览用）。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT po.project_id, po.user_id, u.username, po.created_at "
            "FROM project_owner po LEFT JOIN users u ON u.id=po.user_id "
            "ORDER BY po.created_at DESC").fetchall()
    return [dict(r) for r in rows]


# ---------- 会话 ----------
def create_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    with _connect() as conn:
        conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
                     (token, user_id, time.time()))
    return token


def get_session_user(token: str) -> Optional[int]:
    with _connect() as conn:
        row = conn.execute("SELECT user_id FROM sessions WHERE token=?", (token,)).fetchone()
    return row["user_id"] if row else None


def delete_session(token: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


# ---------- 项目归属 ----------
def claim_project(project_id: str, user_id: int, price: float = 0.0) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO project_owner (project_id, user_id, price, created_at) VALUES (?,?,?,?)",
            (project_id, user_id, price, time.time()))


def project_price(project_id: str) -> float:
    """项目创建时实扣金额（含代理折扣）。老项目无记录返回 0（不可退费）。"""
    with _connect() as conn:
        row = conn.execute("SELECT price FROM project_owner WHERE project_id=?", (project_id,)).fetchone()
    return float(row["price"]) if row and row["price"] is not None else 0.0


def update_project_price(project_id: str, price: float) -> None:
    """更新项目实付金额（生成完成后按实际 AI 字数结算时调用）。"""
    with _connect() as conn:
        conn.execute("UPDATE project_owner SET price=? WHERE project_id=?", (price, project_id))


def find_commission_by_project(project_id: str) -> Optional[dict]:
    """查触发该项目的消费返佣记录（用于退费冲正）。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM commission_log WHERE project_id=? AND kind='consume' "
            "ORDER BY id LIMIT 1",
            (project_id,),
        ).fetchone()
    return dict(row) if row else None


def project_owner(project_id: str) -> Optional[int]:
    with _connect() as conn:
        row = conn.execute("SELECT user_id FROM project_owner WHERE project_id=?", (project_id,)).fetchone()
    return row["user_id"] if row else None


def list_user_projects(user_id: int) -> set[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT project_id FROM project_owner WHERE user_id=?", (user_id,)).fetchall()
    return {r["project_id"] for r in rows}


def all_projects() -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute("SELECT project_id, user_id FROM project_owner").fetchall()
    return {r["project_id"]: r["user_id"] for r in rows}


def delete_project_owner(project_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM project_owner WHERE project_id=?", (project_id,))


# ---------- 退款申请 ----------
def refundable_amount(user_id: int) -> float:
    """可退金额 = 累计充值（管理员手动充值） - 已批准退款。

    注册赠金、代理返佣属于平台赠送/奖励，不可退，不计入可退金额。
    """
    with _connect() as conn:
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM recharges "
            "WHERE user_id=? AND amount>0 AND note LIKE '管理员手动充值%'",
            (user_id,)).fetchone()["s"]
        refunded = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM refund_requests "
            "WHERE user_id=? AND status='approved'",
            (user_id,)).fetchone()["s"]
    return round(max(paid - refunded, 0.0), 2)


def create_refund_request(user_id: int, amount: float, reason: str) -> tuple[bool, str]:
    """提交退款申请。返回 (成功?, 信息)。防重复：有 pending 申请时不允许再提。"""
    if amount <= 0:
        return False, "退款金额必须大于 0"
    with _connect() as conn:
        pending = conn.execute(
            "SELECT id FROM refund_requests WHERE user_id=? AND status='pending'",
            (user_id,)).fetchone()
        if pending:
            return False, "你已有一笔待处理退款申请，请等待站长处理"
        refundable = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM recharges "
            "WHERE user_id=? AND amount>0 AND note LIKE '管理员手动充值%'",
            (user_id,)).fetchone()["s"]
        refunded = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM refund_requests "
            "WHERE user_id=? AND status='approved'",
            (user_id,)).fetchone()["s"]
        if amount > round(refundable - refunded, 2):
            return False, f"可退金额不足（当前可退 {round(refundable - refunded, 2):.2f} 元）"
        conn.execute(
            "INSERT INTO refund_requests (user_id, amount, reason, status, created_at) VALUES (?,?,?,?,?)",
            (user_id, amount, (reason or "").strip()[:200], "pending", time.time()))
    return True, "退款申请已提交，站长处理后到账"


def list_refund_requests(user_id: Optional[int] = None, status: Optional[str] = None) -> list[dict]:
    with _connect() as conn:
        sql = "SELECT * FROM refund_requests WHERE 1=1"
        params: list = []
        if user_id:
            sql += " AND user_id=?"
            params.append(user_id)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT 200"
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def handle_refund(request_id: int, action: str, admin_note: str = "") -> tuple[bool, str]:
    """站长处理退款。action=approve：线下已转账，扣用户余额并标记完成；
    action=reject：拒绝退款并备注原因。"""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM refund_requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            return False, "退款申请不存在"
        if row["status"] != "pending":
            return False, "该申请已处理，请勿重复操作"
        if action == "approve":
            # 从用户余额扣除（余额可能不足申请额，扣到 0 为止）
            bal_row = conn.execute("SELECT balance FROM users WHERE id=?", (row["user_id"],)).fetchone()
            balance = bal_row["balance"] if bal_row else 0.0
            deduct = round(min(row["amount"], balance), 2)
            conn.execute("UPDATE users SET balance = balance - ? WHERE id=?", (deduct, row["user_id"]))
            conn.execute("INSERT INTO recharges (user_id, amount, note, created_at) VALUES (?,?,?,?)",
                         (row["user_id"], -deduct, f"退款给用户（申请#{request_id}）", time.time()))
            conn.execute(
                "UPDATE refund_requests SET status='approved', admin_note=?, handled_at=? WHERE id=?",
                ((admin_note or "").strip()[:200], time.time(), request_id))
            return True, f"已确认退款 {row['amount']:.2f} 元（余额扣除 {deduct:.2f} 元）"
        elif action == "reject":
            conn.execute(
                "UPDATE refund_requests SET status='rejected', admin_note=?, handled_at=? WHERE id=?",
                ((admin_note or "").strip()[:200], time.time(), request_id))
            return True, "已拒绝该退款申请"
        return False, "未知操作"


# 模块加载时初始化
init_db()
