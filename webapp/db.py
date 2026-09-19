# -*- coding: utf-8 -*-
"""
多用户数据层（MySQL）：用户、会话、余额、项目归属、充值记录、代理返佣。

表结构（与旧 SQLite 版一致）：
    users          用户（id, username, pass_hash, email, balance, is_admin, created_at,
                         is_agent, agent_discount, invite_code, referrer_id）
    sessions       登录会话（token, user_id, created_at）
    project_owner  项目归属（project_id -> user_id）
    recharges      余额流水（充值/扣费/返佣都记录在这里，amount 可正可负）
    commission_log 返佣明细（谁给谁返了多少、哪笔消费触发）
    refund_requests 退款申请

连接配置从环境变量读取（.env 或系统环境变量）：
    DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME（默认 mozao）
"""
from __future__ import annotations

import hashlib
import bcrypt
import hmac
import os
import re
import secrets
import time
from pathlib import Path
from typing import Optional

import pymysql
from pymysql.cursors import DictCursor
from pymysql.err import IntegrityError as MyIntegrityError
from dbutils.pooled_db import PooledDB

# ---------- .env 轻量加载（兼容 python -m uvicorn 直启，不依赖 run_web.py） ----------
def _load_env_file() -> None:
    """从项目根目录 .env 读取环境变量（若存在且未设置过）。幂等。"""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_env_file()

# ---------- MySQL 连接配置（均来自环境变量，不硬编码） ----------
DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", "3306") or 3306)
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "mozao")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email))


class _TxConn:
    """pymysql 连接的事务包装：`with conn:` 块内执行 SQL，
    正常退出 commit，异常 rollback，最后关闭连接（语义对齐旧 sqlite3 的 with 块）。"""

    def __init__(self, conn: pymysql.connections.Connection):
        self._conn = conn

    def __enter__(self) -> "_TxConn":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            try:
                self._conn.close()
            except Exception:
                pass

    def execute(self, sql: str, params=None):
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur

    def executescript(self, script: str):
        """逐条执行多条 SQL（MySQL 驱动不支持多语句一次执行）。"""
        cur = self._conn.cursor()
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        return cur


# ---------- MySQL 连接池（多线程并发安全） ----------
_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=20,
            mincached=2,
            maxcached=5,
            blocking=True,
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
            connect_timeout=5,
        )
    return _pool


def _connect() -> _TxConn:
    conn = _get_pool().connection()
    return _TxConn(conn)


def _column_names(conn: _TxConn, table: str) -> set[str]:
    rows = conn.execute(f"SHOW COLUMNS FROM `{table}`").fetchall()
    return {r["Field"] for r in rows}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL,
    pass_hash VARCHAR(200) NOT NULL,
    balance DOUBLE NOT NULL DEFAULT 0,
    is_admin INT NOT NULL DEFAULT 0,
    created_at DOUBLE NOT NULL,
    email VARCHAR(120),
    is_agent INT NOT NULL DEFAULT 0,
    agent_discount DOUBLE NOT NULL DEFAULT 1.0,
    invite_code VARCHAR(50),
    referrer_id INT,
    UNIQUE KEY uk_users_username (username),
    UNIQUE KEY uk_users_email (email),
    UNIQUE KEY uk_users_invite (invite_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""
_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    token VARCHAR(100) PRIMARY KEY,
    user_id INT NOT NULL,
    created_at DOUBLE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""
_PROJECT_OWNER = """
CREATE TABLE IF NOT EXISTS project_owner (
    project_id VARCHAR(200) PRIMARY KEY,
    user_id INT NOT NULL,
    price DOUBLE NOT NULL DEFAULT 0,
    discount DOUBLE NOT NULL DEFAULT 1.0,
    created_at DOUBLE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""
_RECHARGES = """
CREATE TABLE IF NOT EXISTS recharges (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    amount DOUBLE NOT NULL,
    note TEXT,
    created_at DOUBLE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""
_COMMISSION = """
CREATE TABLE IF NOT EXISTS commission_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    agent_id INT NOT NULL,
    buyer_id INT NOT NULL,
    amount DOUBLE NOT NULL,
    kind VARCHAR(20) NOT NULL,
    project_id VARCHAR(200) DEFAULT '',
    note TEXT,
    created_at DOUBLE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""
_REFUNDS = """
CREATE TABLE IF NOT EXISTS refund_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    amount DOUBLE NOT NULL,
    reason TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    admin_note TEXT,
    created_at DOUBLE NOT NULL,
    handled_at DOUBLE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


_SKILLS = """CREATE TABLE IF NOT EXISTS writing_skills (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(80) NOT NULL,
    description VARCHAR(255) DEFAULT '',
    prompt_append TEXT,
    is_active INT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DOUBLE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_USER_SKILLS = """CREATE TABLE IF NOT EXISTS user_skills (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(80) NOT NULL,
    description VARCHAR(255) DEFAULT '',
    prompt_append TEXT,
    role VARCHAR(20) NOT NULL DEFAULT '',
    is_active INT NOT NULL DEFAULT 1,
    created_at DOUBLE NOT NULL,
    KEY idx_us_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def init_db() -> None:
    with _connect() as conn:
        for ddl in (_SCHEMA, _SESSIONS, _PROJECT_OWNER, _RECHARGES, _COMMISSION, _REFUNDS, _SKILLS, _USER_SKILLS):
            conn.execute(ddl)
        # 老表缺列时补列（新库建全表后通常不会命中；幂等保护）
        cols = _column_names(conn, "users")
        if "email" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email VARCHAR(120)")
            conn.execute("ALTER TABLE users ADD UNIQUE KEY uk_users_email (email)")
        if "is_agent" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_agent INT NOT NULL DEFAULT 0")
        if "agent_discount" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN agent_discount DOUBLE NOT NULL DEFAULT 1.0")
        if "invite_code" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN invite_code VARCHAR(50)")
            conn.execute("ALTER TABLE users ADD UNIQUE KEY uk_users_invite (invite_code)")
        if "referrer_id" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN referrer_id INT")
        pcols = _column_names(conn, "project_owner")
        if "price" not in pcols:
            conn.execute("ALTER TABLE project_owner ADD COLUMN price DOUBLE NOT NULL DEFAULT 0")
        if "discount" not in pcols:
            conn.execute("ALTER TABLE project_owner ADD COLUMN discount DOUBLE NOT NULL DEFAULT 1.0")
        # writing_skills 加 role 字段：空=全局，researcher/architect/writer/reviewer=专属某角色
        scol = _column_names(conn, "writing_skills")
        if "role" not in scol:
            conn.execute("ALTER TABLE writing_skills ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE writing_skills ADD KEY idx_ws_role (role)")
        # 老用户补邀请码（无码则按 id 生成 INV+id）
        rows = conn.execute(
            "SELECT id FROM users WHERE invite_code IS NULL OR invite_code=''").fetchall()
        for r in rows:
            code = _make_invite_code(r["id"])
            conn.execute("UPDATE users SET invite_code=%s WHERE id=%s", (code, r["id"]))


def _make_invite_code(user_id: int) -> str:
    """生成邀请码：INV + 用户id（可读、可回溯）。"""
    return f"INV{user_id}"


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def _is_bcrypt(pass_hash: str) -> bool:
    return bool(pass_hash) and pass_hash.startswith(("$2a$", "$2b$", "$2y$"))


def _new_password_hash(password: str) -> str:
    """新密码统一用 bcrypt（自带盐，cost=12）。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _verify_password(password: str, stored: str) -> bool:
    """兼容两种格式：bcrypt 直接验；旧 sha256 存为 '<hex>:<salt>'。"""
    stored = stored or ""
    if _is_bcrypt(stored):
        try:
            return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
        except Exception:
            return False
    pass_hash, _, salt = stored.partition(":")
    if not pass_hash or not salt:
        return False
    return hmac.compare_digest(pass_hash, _hash_password(password, salt))


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
        ex = conn.execute("SELECT id FROM users WHERE email=%s", (email,)).fetchone()
        if ex:
            return False, "该邮箱已被注册"
        try:
            cur = conn.execute(
                "INSERT INTO users (username, pass_hash, email, balance, is_admin, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                (username, _new_password_hash(password), email, 0, int(is_admin), time.time()),
            )
        except MyIntegrityError:
            return False, "用户名已被注册"
        new_id = cur.lastrowid
        code = _make_invite_code(new_id)
        conn.execute("UPDATE users SET invite_code=%s WHERE id=%s", (code, new_id))
        if invite_code and invite_code.strip():
            ok, why = _bind_referrer_inner(conn, new_id, invite_code.strip())
            if not ok:
                return True, f"注册成功（邀请码未生效：{why}）"
    return True, "ok"


# ---------- 代理 / 邀请 ----------
def get_invite_code(user_id: int) -> str:
    """取用户邀请码（没有则补生成）。"""
    with _connect() as conn:
        row = conn.execute("SELECT invite_code FROM users WHERE id=%s", (user_id,)).fetchone()
        if row and row["invite_code"]:
            return row["invite_code"]
        code = _make_invite_code(user_id)
        conn.execute("UPDATE users SET invite_code=%s WHERE id=%s", (code, user_id))
        return code


def set_agent(user_id: int, discount: float = 0.8) -> None:
    """把用户设为代理并设定折扣（0 < discount <= 1）。"""
    discount = max(0.01, min(float(discount), 1.0))
    with _connect() as conn:
        conn.execute("UPDATE users SET is_agent=1, agent_discount=%s WHERE id=%s", (discount, user_id))


def unset_agent(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET is_agent=0, agent_discount=1.0 WHERE id=%s", (user_id,))


def bind_referrer(user_id: int, invite_code: str) -> tuple[bool, str]:
    """用户主动补绑上级（仅当尚无上级时）。invite_code 是上级的邀请码。"""
    with _connect() as conn:
        return _bind_referrer_inner(conn, user_id, invite_code)


def _bind_referrer_inner(conn: _TxConn, user_id: int, invite_code: str) -> tuple[bool, str]:
    """绑定核心逻辑（事务内）。"""
    invite_code = (invite_code or "").strip()
    if not invite_code:
        return False, "邀请码为空"
    row = conn.execute("SELECT id, username FROM users WHERE invite_code=%s", (invite_code,)).fetchone()
    if not row:
        return False, "邀请码不存在"
    referrer_id = row["id"]
    if referrer_id == user_id:
        return False, "不能填自己的邀请码"
    me = conn.execute("SELECT referrer_id FROM users WHERE id=%s", (user_id,)).fetchone()
    if me is None:
        return False, "用户不存在"
    if me["referrer_id"] is not None:
        return False, "你已经绑定了上级，无法更换"
    conn.execute("UPDATE users SET referrer_id=%s WHERE id=%s", (referrer_id, user_id))
    return True, "ok"


def get_referrer(user_id: int) -> Optional[dict]:
    """取上级用户信息（未绑定返回 None）。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT u.id, u.username, u.is_agent, u.agent_discount FROM users u "
            "JOIN users me ON me.referrer_id = u.id WHERE me.id=%s",
            (user_id,)).fetchone()
    return dict(row) if row else None


def list_downline(agent_id: int, limit: int = 100) -> list[dict]:
    """取下级用户列表（按加入时间倒序）。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, username, balance, created_at FROM users WHERE referrer_id=%s ORDER BY id DESC LIMIT %s",
            (agent_id, limit)).fetchall()
    return [dict(r) for r in rows]



def pay_commission(agent_id: int, buyer_id: int, amount: float, kind: str,
                   project_id: str = "", note: str = "") -> bool:
    """给代理返佣：入余额 + 写余额流水 + 写返佣明细。返回是否成功。"""
    if amount <= 0:
        return False
    with _connect() as conn:
        conn.execute("UPDATE users SET balance = balance + %s WHERE id=%s", (amount, agent_id))
        conn.execute("INSERT INTO recharges (user_id, amount, note, created_at) VALUES (%s,%s,%s,%s)",
                     (agent_id, amount, note or f"返佣（{kind}）", time.time()))
        conn.execute(
            "INSERT INTO commission_log (agent_id, buyer_id, amount, kind, project_id, note, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (agent_id, buyer_id, amount, kind, project_id, note, time.time()))
    return True


def list_commissions(user_id: int, limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM commission_log WHERE agent_id=%s ORDER BY id DESC LIMIT %s",
            (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


def sum_commission(user_id: int) -> float:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM commission_log WHERE agent_id=%s", (user_id,)).fetchone()
    return float(row["s"]) if row else 0.0


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
            row = conn.execute("SELECT id, pass_hash FROM users WHERE email=%s", (identifier.lower(),)).fetchone()
        else:
            row = conn.execute("SELECT id, pass_hash FROM users WHERE username=%s", (identifier,)).fetchone()
    if not row:
        return None
    stored = row["pass_hash"] or ""
    if _verify_password(password, stored):
        # 老 sha256 用户首次登录成功后，自动平滑升级为 bcrypt
        if not _is_bcrypt(stored):
            try:
                new_hash = _new_password_hash(password)
                with _connect() as conn:
                    conn.execute("UPDATE users SET pass_hash=%s WHERE id=%s", (new_hash, row["id"]))
            except Exception:
                pass
        return row["id"]
    return None


def get_user(user_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_name(username: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=%s", (username.strip(),)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=%s", ((email or "").strip().lower(),)).fetchone()
    return dict(row) if row else None


def update_password(user_id: int, new_password: str) -> None:
    """重置密码（忘记密码用）。"""
    if not new_password or len(new_password) < 4:
        return
    with _connect() as conn:
        conn.execute("UPDATE users SET pass_hash=%s WHERE id=%s",
                     (_new_password_hash(new_password), user_id))


def delete_user_sessions(user_id: int) -> None:
    """删除某用户全部会话（重置密码后强制重新登录）。"""
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id=%s", (user_id,))


def change_balance(user_id: int, delta: float, note: str = "") -> float:
    """给用户加/扣余额（delta 可为负），返回新余额。
    P0 修复：SELECT ... FOR UPDATE 行锁 + 同一事务内读改写+流水，避免并发丢更新。
    （_connect 的 context manager 在无异常时自动 commit，异常 rollback）"""
    with _connect() as conn:
        row = conn.execute("SELECT balance FROM users WHERE id=%s FOR UPDATE", (user_id,)).fetchone()
        old = float(row["balance"]) if row else 0.0
        new_bal = round(old + delta + 1e-9, 2)
        conn.execute("UPDATE users SET balance = %s WHERE id=%s", (new_bal, user_id))
        conn.execute("INSERT INTO recharges (user_id, amount, note, created_at) VALUES (%s,%s,%s,%s)",
                     (user_id, delta, note, time.time()))
    return new_bal


def list_recharges(user_id: Optional[int] = None, limit: int = 50) -> list[dict]:
    with _connect() as conn:
        if user_id:
            rows = conn.execute(
                "SELECT * FROM recharges WHERE user_id=%s ORDER BY id DESC LIMIT %s",
                (user_id, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM recharges ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
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
        "total_recharge": float(r["recharge"]),
        "total_consume": float(r["consume"]),
        "total_bonus": float(r["bonus"]),
        "total_commission": float(c),
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
        conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (%s,%s,%s)",
                     (token, user_id, time.time()))
    return token


def get_session_user(token: str) -> Optional[int]:
    with _connect() as conn:
        row = conn.execute("SELECT user_id FROM sessions WHERE token=%s", (token,)).fetchone()
    return row["user_id"] if row else None


def delete_session(token: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token=%s", (token,))


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


# 模块加载时初始化（建表 + 老数据补全）
init_db()


# ---------- 写作风格 Skill ----------

def list_skills(only_active: bool = False, role: str = None) -> list[dict]:
    """列出 skill。role=None 返回全部；role='' 只返回全局（空）；role='writer' 等返回专属。"""
    sql = "SELECT * FROM writing_skills"
    conds = []
    if only_active:
        conds.append("is_active=1")
    if role is not None:
        # role='writer' → 查 role='writer'；role='' → 查 role=''（全局）
        conds.append("role=%s")
        params = [role]
    else:
        params = []
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY sort_order ASC, id ASC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_skill(skill_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM writing_skills WHERE id=%s", (skill_id,)).fetchone()
    return dict(row) if row else None


def create_skill(name: str, description: str, prompt_append: str,
                 is_active: int = 1, sort_order: int = 0, role: str = "") -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO writing_skills (name, description, prompt_append, is_active, sort_order, role, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (name, description, prompt_append, is_active, sort_order, role, time.time()))
        return cur.lastrowid


def update_skill(skill_id: int, **fields) -> bool:
    if not fields:
        return False
    allowed = {"name", "description", "prompt_append", "is_active", "sort_order", "role"}
    sets = []
    vals = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=%s")
            vals.append(v)
    if not sets:
        return False
    vals.append(skill_id)
    with _connect() as conn:
        conn.execute(f"UPDATE writing_skills SET {','.join(sets)} WHERE id=%s", vals)
    return True


def delete_skill(skill_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM writing_skills WHERE id=%s", (skill_id,))



# ---------- 用户自定义 Skills ----------

def list_user_skills(user_id: int, only_active: bool = False) -> list[dict]:
    sql = "SELECT * FROM user_skills WHERE user_id=%s"
    if only_active:
        sql += " AND is_active=1"
    sql += " ORDER BY id DESC"
    with _connect() as conn:
        rows = conn.execute(sql, (user_id,)).fetchall()
        return [dict(r) for r in rows]

def get_user_skill(skill_id: int, user_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM user_skills WHERE id=%s AND user_id=%s",
                           (skill_id, user_id)).fetchone()
        return dict(row) if row else None

def create_user_skill(user_id: int, name: str, description: str,
                      prompt_append: str, role: str = "", is_active: int = 1) -> int:
    import time
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO user_skills (user_id, name, description, prompt_append, role, is_active, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (user_id, name, description, prompt_append, role, is_active, time.time()))
        return cur.lastrowid

def update_user_skill(skill_id: int, user_id: int, **fields) -> bool:
    allowed = {"name", "description", "prompt_append", "role", "is_active"}
    sets = []
    vals = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=%s")
            vals.append(v)
    if not sets:
        return False
    vals.extend([skill_id, user_id])
    with _connect() as conn:
        cur = conn.execute(f"UPDATE user_skills SET {','.join(sets)} WHERE id=%s AND user_id=%s", vals)
        return cur.rowcount > 0

def delete_user_skill(skill_id: int, user_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM user_skills WHERE id=%s AND user_id=%s", (skill_id, user_id))

def list_user_skills_by_project(project_id: str) -> list[dict]:
    """根据项目ID查用户skills：通过project_owner表找到user_id。"""
    with _connect() as conn:
        row = conn.execute("SELECT user_id FROM project_owner WHERE project_id=%s", (project_id,)).fetchone()
        if not row:
            return []
        uid = row["user_id"]
        rows = conn.execute(
            "SELECT * FROM user_skills WHERE user_id=%s AND is_active=1 ORDER BY id DESC", (uid,)).fetchall()
        return [dict(r) for r in rows]

