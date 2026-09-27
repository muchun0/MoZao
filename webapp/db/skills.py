# -*- coding: utf-8 -*-
"""技能域数据访问：平台写作技能、用户自定义技能、技能市场。"""
from __future__ import annotations

import time
from typing import Optional

from .base import _connect

__all__ = [
    "list_skills", "get_skill", "create_skill", "update_skill", "delete_skill",
    "list_user_skills", "get_user_skill", "create_user_skill", "update_user_skill",
    "delete_user_skill", "list_user_skills_by_project",
    "list_market_skills", "get_market_skill", "publish_market_skill",
    "install_market_skill", "unpublish_market_skill",
]


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
