# -*- coding: utf-8 -*-
"""
数据层基础设施：MySQL 连接池、事务包装、表结构 DDL 与建表初始化（init_db）。

连接配置从环境变量读取（.env 或系统环境变量）：
    DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME（默认 mozao）
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor
from dbutils.pooled_db import PooledDB

from ..logger import get_logger
from ..secret_encrypt import encrypt_secret

logger = get_logger("db")

# ---------- .env 加载（兼容 python -m uvicorn 直启，不依赖 run_web.py） ----------
from dotenv import load_dotenv as _load_dotenv

_load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=False)

# ---------- MySQL 连接配置（均来自环境变量，不硬编码） ----------
DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", "3306") or 3306)
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "mozao")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

__all__ = [
    "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME",
    "_TxConn", "_connect", "_column_names", "_get_pool", "_valid_email",
    "init_db",
]


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
    reference_text TEXT,
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

# Agent 全链路中间状态（模块1）：单次用户请求（trace_id）下所有 Agent 动作明细。
# 覆盖每轮 LLM 调用的 Prompt/原始返回/Token 消耗/耗时/错误栈，支撑问题回溯、
# 阶段2 监控仪表盘（成功率/单步耗时按 scene 聚合）、用户维度行为与质量分析。
_AGENT_TRACE = """CREATE TABLE IF NOT EXISTS agent_trace (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trace_id VARCHAR(64) NOT NULL,
    project_id VARCHAR(200) NOT NULL DEFAULT '',
    user_id INT NOT NULL DEFAULT 0,
    scene VARCHAR(50) NOT NULL DEFAULT '',
    step_type VARCHAR(30) NOT NULL DEFAULT '',
    step_order INT NOT NULL DEFAULT 0,
    step_name VARCHAR(200) DEFAULT '',
    system_prompt MEDIUMTEXT,
    user_input MEDIUMTEXT,
    raw_output MEDIUMTEXT,
    token_in INT NOT NULL DEFAULT 0,
    token_out INT NOT NULL DEFAULT 0,
    token_total INT NOT NULL DEFAULT 0,
    duration_ms INT NOT NULL DEFAULT 0,
    success INT NOT NULL DEFAULT 1,
    error_info TEXT,
    model VARCHAR(100) DEFAULT '',
    extra JSON,
    created_at DOUBLE NOT NULL,
    KEY idx_at_trace (trace_id, step_order),
    KEY idx_at_project (project_id, id),
    KEY idx_at_user (user_id, id),
    KEY idx_at_scene (scene, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def init_db() -> None:
    # 延迟导入：避免 base <-> 业务子模块的循环依赖；
    # 本函数仅在 webapp/db/__init__.py 末尾（全部子模块加载完成后）调用一次。
    from .style_roles import _seed_style_roles
    from .teardowns import _ensure_teardown_user_id
    from .users import _make_invite_code

    with _connect() as conn:
        for ddl in (_SCHEMA, _SESSIONS, _PROJECT_OWNER, _RECHARGES, _COMMISSION, _REFUNDS, _SKILLS, _USER_SKILLS, _SKILL_MARKET,
                    _STYLE_ROLES, _STYLE_ROLE_SKILLS, _TEARDOWN, _AGENT_TRACE):
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
        # style_roles 加 reference_text（P2-1 few-shot 风格范文，可空；存量数据兼容）
        if "reference_text" not in _column_names(conn, "style_roles"):
            conn.execute("ALTER TABLE style_roles ADD COLUMN reference_text TEXT")
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
