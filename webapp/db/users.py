# -*- coding: utf-8 -*-
"""用户域数据访问：密码、注册、代理/邀请、返佣、认证、用户 LLM、会话、余额、管理统计。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Optional

import bcrypt
from pymysql.err import IntegrityError as MyIntegrityError

from .base import _TxConn, _connect, _valid_email
from ..secret_encrypt import decrypt_secret, encrypt_secret

__all__ = [
    "create_user", "get_invite_code", "set_agent", "unset_agent", "bind_referrer",
    "get_referrer", "list_downline", "pay_commission", "list_commissions",
    "sum_commission", "list_agents", "authenticate", "get_user_llm", "set_user_llm",
    "get_user", "get_user_by_name", "get_user_by_email", "update_password",
    "delete_user_sessions", "change_balance", "list_recharges", "admin_stats",
    "list_users_admin", "create_session", "get_session_user", "delete_session",
]


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


# ---------- 代理返佣 ----------
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


# ---------- 认证 ----------
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


# ---------- 用户 LLM ----------
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


# ---------- 用户查询 ----------
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


# ---------- 密码 / 会话 ----------
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


# ---------- 余额 / 充值 ----------
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
