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

from .secret_encrypt import decrypt_secret, encrypt_secret
from .logger import get_logger

logger = get_logger("db")

# ---------- .env 加载（兼容 python -m uvicorn 直启，不依赖 run_web.py） ----------
from dotenv import load_dotenv as _load_dotenv
_load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

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

_SKILL_MARKET = """CREATE TABLE IF NOT EXISTS skill_market (
    id INT AUTO_INCREMENT PRIMARY KEY,
    author_id INT NOT NULL,
    name VARCHAR(80) NOT NULL,
    description VARCHAR(255) DEFAULT '',
    prompt_append TEXT,
    role VARCHAR(20) NOT NULL DEFAULT '',
    downloads INT NOT NULL DEFAULT 0,
    created_at DOUBLE NOT NULL,
    KEY idx_sm_author (author_id),
    KEY idx_sm_role (role)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_STYLE_ROLES = """CREATE TABLE IF NOT EXISTS style_roles (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL DEFAULT 0,
    name VARCHAR(80) NOT NULL,
    description VARCHAR(255) DEFAULT '',
    role_prompt TEXT,
    is_active INT NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DOUBLE NOT NULL,
    KEY idx_sr_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

_STYLE_ROLE_SKILLS = """CREATE TABLE IF NOT EXISTS style_role_skills (
    id INT AUTO_INCREMENT PRIMARY KEY,
    role_id INT NOT NULL,
    skill_type VARCHAR(8) NOT NULL DEFAULT 'admin',
    skill_id INT NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    created_at DOUBLE NOT NULL,
    UNIQUE KEY uk_srs_role_skill (role_id, skill_type, skill_id),
    KEY idx_srs_role (role_id),
    KEY idx_srs_skill (skill_type, skill_id)
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

_TEARDOWN = """CREATE TABLE IF NOT EXISTS book_teardown (
    id INT AUTO_INCREMENT PRIMARY KEY,
    genre VARCHAR(50) NOT NULL DEFAULT '',
    title VARCHAR(120) NOT NULL,
    summary VARCHAR(1000) DEFAULT '',
    content TEXT,
    source_project VARCHAR(64) DEFAULT '',
    source_type VARCHAR(10) NOT NULL DEFAULT 'research',
    view_count INT NOT NULL DEFAULT 0,
    status INT NOT NULL DEFAULT 1,
    created_at DOUBLE NOT NULL,
    updated_at DOUBLE NOT NULL,
    KEY idx_td_genre (genre),
    KEY idx_td_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def init_db() -> None:
    with _connect() as conn:
        for ddl in (_SCHEMA, _SESSIONS, _PROJECT_OWNER, _RECHARGES, _COMMISSION, _REFUNDS, _SKILLS, _USER_SKILLS, _SKILL_MARKET,
                    _STYLE_ROLES, _STYLE_ROLE_SKILLS, _TEARDOWN):
            conn.execute(ddl)
        _ensure_teardown_user_id()
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
        # 用户自配模型（填了即用自己的 key，平台不扣费）
        if "llm_provider" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN llm_provider VARCHAR(30) NOT NULL DEFAULT ''")
        if "llm_api_key" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN llm_api_key VARCHAR(200) NOT NULL DEFAULT ''")
        if "llm_base_url" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN llm_base_url VARCHAR(200) NOT NULL DEFAULT ''")
        if "llm_model" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN llm_model VARCHAR(100) NOT NULL DEFAULT ''")
        usk_cols = _column_names(conn, "user_skills")
        if "market_id" not in usk_cols:
            conn.execute("ALTER TABLE user_skills ADD COLUMN market_id INT NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE user_skills ADD KEY idx_us_market (market_id)")
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
        # style_roles 加 user_id 归属：0=平台内置，>0=用户自建
        sr_cols = _column_names(conn, "style_roles")
        if "user_id" not in sr_cols:
            conn.execute("ALTER TABLE style_roles ADD COLUMN user_id INT NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE style_roles ADD KEY idx_sr_user (user_id)")
        # style_role_skills 加 skill_type：admin=writing_skills / user=user_skills
        srs_cols = _column_names(conn, "style_role_skills")
        if "skill_type" not in srs_cols:
            conn.execute("ALTER TABLE style_role_skills ADD COLUMN skill_type VARCHAR(8) NOT NULL DEFAULT 'admin'")
            try:
                conn.execute("ALTER TABLE style_role_skills DROP INDEX uk_srs_role_skill")
            except Exception:
                pass
            conn.execute("ALTER TABLE style_role_skills ADD UNIQUE KEY uk_srs_role_skill (role_id, skill_type, skill_id)")
        # 老用户补邀请码（无码则按 id 生成 INV+id）
        rows = conn.execute(
            "SELECT id FROM users WHERE invite_code IS NULL OR invite_code=''").fetchall()
        for r in rows:
            code = _make_invite_code(r["id"])
            conn.execute("UPDATE users SET invite_code=%s WHERE id=%s", (code, r["id"]))
        # 存量明文 BYOK Key 迁移为密文（enc:v1: 前缀幂等；密钥缺失时跳过并警告）
        if "llm_api_key" in cols:
            plain_rows = conn.execute(
                "SELECT id, llm_api_key FROM users "
                "WHERE llm_api_key IS NOT NULL AND llm_api_key<>'' "
                "AND llm_api_key NOT LIKE 'enc:v1:%'").fetchall()
            for r in plain_rows:
                enc = encrypt_secret(r["llm_api_key"])
                if enc:
                    conn.execute("UPDATE users SET llm_api_key=%s WHERE id=%s", (enc, r["id"]))
                else:
                    logger.warning("BYOK Key 加密迁移跳过: user_id=%d", r["id"])
        # 风格角色种子化（幂等：仅当表为空时从 skills_library/style-references 导入）
        try:
            _seed_style_roles()
        except Exception as e:
            logger.warning("风格角色种子化跳过（不影响启动）: %s", e)


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


def get_user_llm(user_id: int) -> dict:
    """返回用户自配 LLM（未配则空字符串）。llm_api_key 解密后返回。

    键名统一为 provider/api_key/base_url/model（与 server.py 调用方契约一致）。
    P0 修复：原实现命中时返回 llm_* 前缀键，与调用方期望不符，导致
    _user_llm_cfg 永远判"未配置"，BYOK 自配 key 不生效且按平台 key 计费。
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT llm_provider, llm_api_key, llm_base_url, llm_model FROM users WHERE id=%s",
            (user_id,)).fetchone()
    if not row:
        return {"provider": "", "api_key": "", "base_url": "", "model": ""}
    return {
        "provider": row.get("llm_provider") or "",
        "api_key": decrypt_secret(row.get("llm_api_key") or ""),
        "base_url": row.get("llm_base_url") or "",
        "model": row.get("llm_model") or "",
    }


def set_user_llm(user_id: int, provider: str, api_key: str, base_url: str, model: str) -> None:
    """保存用户自配 LLM。api_key 加密后落库（DB 泄露不暴露明文）。"""
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET llm_provider=%s, llm_api_key=%s, llm_base_url=%s, llm_model=%s WHERE id=%s",
            (provider, encrypt_secret(api_key), base_url, model, user_id))


def get_user(user_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
    return _decrypt_user_row(row)


def get_user_by_name(username: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=%s", (username.strip(),)).fetchone()
    return _decrypt_user_row(row)


def get_user_by_email(email: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=%s", ((email or "").strip().lower(),)).fetchone()
    return _decrypt_user_row(row)


def _decrypt_user_row(row) -> Optional[dict]:
    """get_user* 统一出口：llm_api_key 解密后返回，保持"读取即明文"语义。"""
    if not row:
        return None
    d = dict(row)
    if d.get("llm_api_key"):
        d["llm_api_key"] = decrypt_secret(d["llm_api_key"])
    return d


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


# 模块加载时初始化（建表 + 老数据补全）。
# 注意：必须放在全部函数定义之后——init_db 内的 _seed_style_roles 等
# 种子化函数定义在本文件尾部，若提前调用会因未定义而失败。
# init_db() 见文件末尾。


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

# ---------- Skill 市场 ----------
def list_market_skills(role: str = "", user_id: int = 0) -> list[dict]:
    """市场列表；user_id 用于标记该用户是否已安装。"""
    sql = ("SELECT m.id, m.author_id, m.name, m.description, m.role, m.downloads, m.created_at, "
           "u.username AS author_name "
           "FROM skill_market m LEFT JOIN users u ON u.id=m.author_id ")
    args = []
    if role:
        sql += "WHERE m.role=%s "
        args.append(role)
    sql += "ORDER BY m.downloads DESC, m.id DESC LIMIT 200"
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        result = [dict(r) for r in rows]
        installed = set()
        if user_id:
            installed = {r["market_id"] for r in conn.execute(
                "SELECT DISTINCT market_id FROM user_skills WHERE user_id=%s AND market_id>0",
                (user_id,)).fetchall()}
        for r in result:
            r["installed"] = r["id"] in installed
    return result


def get_market_skill(market_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM skill_market WHERE id=%s", (market_id,)).fetchone()
    return dict(row) if row else None


def publish_market_skill(author_id: int, name: str, description: str,
                         prompt_append: str, role: str) -> int:
    import time
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO skill_market (author_id, name, description, prompt_append, role, downloads, created_at) "
            "VALUES (%s,%s,%s,%s,%s,0,%s)",
            (author_id, name, description, prompt_append, role, time.time()))
        return cur.lastrowid


def install_market_skill(user_id: int, market_id: int) -> int:
    """把市场 skill 装到我的 user_skills。幂等：已装过返回旧 id。"""
    m = get_market_skill(market_id)
    if not m:
        raise RuntimeError("市场技能不存在")
    with _connect() as conn:
        old = conn.execute(
            "SELECT id FROM user_skills WHERE user_id=%s AND market_id=%s",
            (user_id, market_id)).fetchone()
        if old:
            return old["id"]
        sid = create_user_skill(user_id=user_id, name=m["name"], description=m["description"],
                                prompt_append=m["prompt_append"], role=m["role"], is_active=1)
        # create_user_skill 不写 market_id，补一下
        conn.execute("UPDATE user_skills SET market_id=%s WHERE id=%s", (market_id, sid))
        conn.execute("UPDATE skill_market SET downloads=downloads+1 WHERE id=%s", (market_id,))
        return sid


def unpublish_market_skill(market_id: int, author_id: int) -> bool:
    m = get_market_skill(market_id)
    if not m or m["author_id"] != author_id:
        return False
    with _connect() as conn:
        conn.execute("DELETE FROM skill_market WHERE id=%s", (market_id,))
    return True


# ---------- 写作风格角色（Style Role：把写作风格抽象为 agent 角色，可挂载技能） ----------

def list_style_roles(only_active: bool = False, owner_id: int | None = None,
                     with_skills: bool = True) -> list[dict]:
    """列出风格角色。

    owner_id=None：全部（管理后台）；
    owner_id=N：平台内置（user_id=0）+ 该用户自建（user_id=N）。
    with_skills=True 时附带每个角色挂载的技能名列表。
    """
    sql = "SELECT * FROM style_roles"
    conds: list[str] = []
    params: list = []
    if only_active:
        conds.append("is_active=1")
    if owner_id is not None:
        conds.append("(user_id=0 OR user_id=%s)")
        params.append(owner_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY sort_order ASC, id ASC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        result = [dict(r) for r in rows]
        if with_skills and result:
            for r in result:
                r["skills"] = [dict(s) for s in conn.execute(
                    "SELECT rs.skill_type, rs.skill_id, "
                    "CASE WHEN rs.skill_type='user' THEN us.name ELSE s.name END AS skill_name "
                    "FROM style_role_skills rs "
                    "LEFT JOIN writing_skills s ON rs.skill_type='admin' AND s.id=rs.skill_id "
                    "LEFT JOIN user_skills us ON rs.skill_type='user' AND us.id=rs.skill_id "
                    "WHERE rs.role_id=%s ORDER BY rs.sort_order ASC, rs.id ASC",
                    (r["id"],)).fetchall()]
    return result


def get_style_role(role_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM style_roles WHERE id=%s", (role_id,)).fetchone()
    return dict(row) if row else None


def create_style_role(user_id: int, name: str, description: str = "", role_prompt: str = "",
                      is_active: int = 1, sort_order: int = 0) -> int:
    """创建风格角色。user_id=0 表示平台内置（管理员创建/种子化）。"""
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO style_roles (user_id, name, description, role_prompt, is_active, sort_order, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (user_id, name, description, role_prompt, is_active, sort_order, time.time()))
        return cur.lastrowid


def update_style_role(role_id: int, **fields) -> bool:
    if not fields:
        return False
    allowed = {"name", "description", "role_prompt", "is_active", "sort_order"}
    sets = []
    vals = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=%s")
            vals.append(v)
    if not sets:
        return False
    vals.append(role_id)
    with _connect() as conn:
        cur = conn.execute(f"UPDATE style_roles SET {','.join(sets)} WHERE id=%s", vals)
        return cur.rowcount > 0


def delete_style_role(role_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM style_role_skills WHERE role_id=%s", (role_id,))
        conn.execute("DELETE FROM style_roles WHERE id=%s", (role_id,))


def list_style_role_skills(role_id: int) -> list[dict]:
    """某风格角色已挂载的技能（含 prompt_append，供组装提示词）。

    返回 [{skill_type, skill_id, skill_name, prompt_append, sort_order}]。
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT rs.skill_type, rs.skill_id, rs.sort_order, "
            "CASE WHEN rs.skill_type='user' THEN us.name ELSE s.name END AS skill_name, "
            "CASE WHEN rs.skill_type='user' THEN us.prompt_append ELSE s.prompt_append END AS prompt_append "
            "FROM style_role_skills rs "
            "LEFT JOIN writing_skills s ON rs.skill_type='admin' AND s.id=rs.skill_id "
            "LEFT JOIN user_skills us ON rs.skill_type='user' AND us.id=rs.skill_id "
            "WHERE rs.role_id=%s ORDER BY rs.sort_order ASC, rs.id ASC",
            (role_id,)).fetchall()
    return [dict(r) for r in rows if r.get("skill_name")]


def mount_skill_to_role(role_id: int, skill_type: str, skill_id: int) -> int:
    """把技能挂载到风格角色。幂等：已挂载返回已有 id。

    skill_type='admin' 指 writing_skills（平台技能）；'user' 指 user_skills（用户自建）。
    """
    role = get_style_role(role_id)
    if not role:
        raise RuntimeError("风格角色不存在")
    skill_type = "user" if skill_type == "user" else "admin"
    if skill_type == "admin":
        if not get_skill(skill_id):
            raise RuntimeError("写作技能不存在")
    else:
        # 用户技能必须存在且属于该角色所有者（平台角色只能由管理员挂用户技能的场景除外）
        with _connect() as conn:
            us = conn.execute(
                "SELECT id FROM user_skills WHERE id=%s AND user_id=%s",
                (skill_id, role["user_id"])).fetchone()
        if not us:
            raise RuntimeError("用户技能不存在或不属于你")
    with _connect() as conn:
        old = conn.execute(
            "SELECT id FROM style_role_skills WHERE role_id=%s AND skill_type=%s AND skill_id=%s",
            (role_id, skill_type, skill_id)).fetchone()
        if old:
            return old["id"]
        cur = conn.execute(
            "INSERT INTO style_role_skills (role_id, skill_type, skill_id, sort_order, created_at) "
            "VALUES (%s,%s,%s,%s,%s)",
            (role_id, skill_type, skill_id, 0, time.time()))
        return cur.lastrowid


def unmount_skill_from_role(role_id: int, skill_type: str, skill_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM style_role_skills WHERE role_id=%s AND skill_type=%s AND skill_id=%s",
                     (role_id, skill_type, skill_id))


def get_style_role_assembled(role_id: int) -> dict | None:
    """组装风格角色完整提示词：角色人设 + 已挂载技能，供创建项目时快照进 config。

    返回 {"id", "name", "description", "role_prompt", "skills_text", "assembled"}；
    角色不存在或未启用返回 None。
    """
    role = get_style_role(role_id)
    if not role or not role.get("is_active"):
        return None
    parts = [role.get("role_prompt") or ""]
    blocks = []
    for sk in list_style_role_skills(role_id):
        if sk.get("prompt_append"):
            tag = "用户技能" if sk.get("skill_type") == "user" else "技能"
            blocks.append(f"【{tag}：{sk['skill_name']}】\n{sk['prompt_append']}")
    skills_text = "\n\n".join(blocks)
    if skills_text:
        parts.append("## 本风格角色挂载技能（必须严格遵循）\n" + skills_text)
    return {
        "id": role["id"],
        "name": role["name"],
        "description": role.get("description", ""),
        "role_prompt": role.get("role_prompt", ""),
        "skills_text": skills_text,
        "assembled": "\n\n".join(p for p in parts if p.strip()),
    }


def _seed_style_roles() -> None:
    """幂等种子化：style_roles 为空时，把 skills_library/style-references/*.md
    导入为平台内置风格角色（user_id=0，人设取文档全文，元信息取 frontmatter）。"""
    with _connect() as conn:
        cnt = conn.execute("SELECT COUNT(*) AS c FROM style_roles").fetchone()["c"]
        if cnt:
            return
    from pathlib import Path
    lib_dir = Path(__file__).resolve().parent.parent / "skills_library" / "style-references"
    if not lib_dir.is_dir():
        return
    files = sorted(lib_dir.glob("*.md"))
    if not files:
        return
    for f in files:
        text = f.read_text(encoding="utf-8").strip()
        if not text:
            continue
        title, source = _parse_frontmatter(text)
        # 名称：frontmatter title 优先，否则文件名去掉日期后缀
        stem = f.stem
        for suf in ("-20260916", "-20260917"):
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
                break
        name = (title or stem).strip()[:80]
        desc = f"来源：{source or 'skills_library'}"
        create_style_role(user_id=0, name=name, description=desc[:255], role_prompt=text)
        logger.info("种子化风格角色: %s <- %s", name, f.name)


def _parse_frontmatter(text: str) -> tuple[str, str]:
    """解析 Markdown 头部 YAML frontmatter，返回 (title, source)。"""
    title = ""
    source = ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            head = text[3:end]
            for line in head.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    k = k.strip().lower()
                    v = v.strip().strip("'\"")
                    if k == "title" and not title:
                        title = v
                    elif k == "source" and not source:
                        source = v
    return title, source



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

# ---------- 爆款拆书库（book_teardown：调研成果/内置知识库的沉淀与浏览） ----------

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


# 模块加载时初始化（建表 + 老数据补全 + 风格角色种子化）。
# 必须在全部函数定义之后调用（_seed_style_roles 定义于本文件末尾）。
init_db()
