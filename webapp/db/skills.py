# -*- coding: utf-8 -*-
"""技能域数据访问：平台写作技能、用户自定义技能、技能市场。

REQ-技能统一 阶段二：三类技能收敛到统一表 `skills`（source ∈ platform/user/market），
旧 4 表保留不 DROP（迁移回退），运行时读写全部走本表。
对外函数签名与返回字段不变（platform/user 行带 `user_id` 别名、market 行带 `author_id` 别名），
前端与调用方零改动。
"""
from __future__ import annotations

import time
from typing import Optional

from .base import _connect

__all__ = [
    "list_skills", "get_skill", "get_skill_by_name", "create_skill", "update_skill", "delete_skill",
    "list_user_skills", "get_user_skill", "create_user_skill", "update_user_skill",
    "delete_user_skill", "list_user_skills_by_project",
    "list_market_skills", "get_market_skill", "publish_market_skill",
    "install_market_skill", "unpublish_market_skill",
]

# 平台/用户技能通用列（user_id 用 owner_id 别名，前端靠它区分"我的/平台"）
_SKILL_COLS = "id, name, description, prompt_append, role, applicable_stages, version, " \
              "is_active, sort_order, created_at"


# ---------- 平台写作 Skill（source='platform'，owner_id=0） ----------
def list_skills(only_active: bool = False, role: str = None) -> list[dict]:
    """列出平台技能。role=None 返回全部；role='' 只返回全局（空）；role='writer' 等返回专属。"""
    sql = f"SELECT 0 AS user_id, {_SKILL_COLS} FROM skills WHERE source='platform'"
    conds = []
    params: list = []
    if only_active:
        conds.append("is_active=1")
    if role is not None:
        # role='writer' → 查 role='writer'；role='' → 查 role=''（全局）
        conds.append("role=%s")
        params.append(role)
    if conds:
        sql += " AND " + " AND ".join(conds)
    sql += " ORDER BY sort_order ASC, id ASC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_skill(skill_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT 0 AS user_id, {_SKILL_COLS} FROM skills WHERE source='platform' AND id=%s",
            (skill_id,)).fetchone()
    return dict(row) if row else None


def get_skill_by_name(name: str) -> dict | None:
    """按名称查平台技能（source='platform'），供 skills_library 同步做幂等 upsert。"""
    with _connect() as conn:
        row = conn.execute(
            f"SELECT 0 AS user_id, {_SKILL_COLS} FROM skills "
            f"WHERE source='platform' AND name=%s LIMIT 1",
            (name,)).fetchone()
    return dict(row) if row else None


def create_skill(name: str, description: str, prompt_append: str,
                 is_active: int = 1, sort_order: int = 0, role: str = "",
                 applicable_stages: str = "", version: str = "1") -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO skills (source, owner_id, name, description, prompt_append, role, "
            "applicable_stages, version, is_active, sort_order, created_at) "
            "VALUES ('platform', 0, %s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (name, description, prompt_append, role, applicable_stages, version,
             is_active, sort_order, time.time()))
        return cur.lastrowid


def update_skill(skill_id: int, **fields) -> bool:
    if not fields:
        return False
    allowed = {"name", "description", "prompt_append", "is_active", "sort_order",
               "role", "applicable_stages", "version"}
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
        conn.execute(f"UPDATE skills SET {','.join(sets)} WHERE source='platform' AND id=%s", vals)
    return True


def delete_skill(skill_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM skills WHERE source='platform' AND id=%s", (skill_id,))


# ---------- 用户自定义 Skills（source='user'，owner_id=用户） ----------
def list_user_skills(user_id: int, only_active: bool = False) -> list[dict]:
    sql = f"SELECT owner_id AS user_id, {_SKILL_COLS}, market_id FROM skills " \
          f"WHERE source='user' AND owner_id=%s"
    if only_active:
        sql += " AND is_active=1"
    sql += " ORDER BY id DESC"
    with _connect() as conn:
        rows = conn.execute(sql, (user_id,)).fetchall()
        return [dict(r) for r in rows]


def get_user_skill(skill_id: int, user_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT owner_id AS user_id, {_SKILL_COLS}, market_id FROM skills "
            f"WHERE source='user' AND id=%s AND owner_id=%s",
            (skill_id, user_id)).fetchone()
        return dict(row) if row else None


def create_user_skill(user_id: int, name: str, description: str,
                      prompt_append: str, role: str = "", applicable_stages: str = "",
                      is_active: int = 1, market_id: int = 0) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO skills (source, owner_id, name, description, prompt_append, role, "
            "applicable_stages, is_active, market_id, created_at) "
            "VALUES ('user', %s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (user_id, name, description, prompt_append, role, applicable_stages,
             is_active, market_id, time.time()))
        return cur.lastrowid


def update_user_skill(skill_id: int, user_id: int, **fields) -> bool:
    allowed = {"name", "description", "prompt_append", "role", "applicable_stages", "is_active"}
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
        cur = conn.execute(
            f"UPDATE skills SET {','.join(sets)} WHERE source='user' AND id=%s AND owner_id=%s",
            vals)
        return cur.rowcount > 0


def delete_user_skill(skill_id: int, user_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM skills WHERE source='user' AND id=%s AND owner_id=%s",
                     (skill_id, user_id))


def list_user_skills_by_project(project_id: str) -> list[dict]:
    """根据项目ID查用户技能：通过 project_owner 表找到 user_id。"""
    with _connect() as conn:
        row = conn.execute("SELECT user_id FROM project_owner WHERE project_id=%s",
                           (project_id,)).fetchone()
        if not row:
            return []
        uid = row["user_id"]
        rows = conn.execute(
            f"SELECT owner_id AS user_id, {_SKILL_COLS}, market_id FROM skills "
            f"WHERE source='user' AND owner_id=%s AND is_active=1 ORDER BY id DESC",
            (uid,)).fetchall()
        return [dict(r) for r in rows]


# ---------- Skill 市场（source='market'，owner_id=作者） ----------
def list_market_skills(role: str = "", user_id: int = 0) -> list[dict]:
    """市场列表；user_id 用于标记该用户是否已安装。"""
    sql = ("SELECT m.owner_id AS author_id, m.id, m.name, m.description, m.role, m.applicable_stages, "
           "m.downloads, m.created_at, "
           "u.username AS author_name "
           "FROM skills m LEFT JOIN users u ON u.id=m.owner_id "
           "WHERE m.source='market' ")
    args: list = []
    if role:
        sql += "AND m.role=%s "
        args.append(role)
    sql += "ORDER BY m.downloads DESC, m.id DESC LIMIT 200"
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        result = [dict(r) for r in rows]
        installed = set()
        if user_id:
            installed = {r["market_id"] for r in conn.execute(
                "SELECT DISTINCT market_id FROM skills WHERE source='user' AND owner_id=%s AND market_id>0",
                (user_id,)).fetchall()}
        for r in result:
            r["installed"] = r["id"] in installed
    return result


def get_market_skill(market_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT *, owner_id AS author_id FROM skills WHERE source='market' AND id=%s",
            (market_id,)).fetchone()
    return dict(row) if row else None


def publish_market_skill(author_id: int, name: str, description: str,
                         prompt_append: str, role: str, applicable_stages: str = "") -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO skills (source, owner_id, name, description, prompt_append, role, "
            "applicable_stages, downloads, created_at) "
            "VALUES ('market', %s,%s,%s,%s,%s,%s,0,%s)",
            (author_id, name, description, prompt_append, role, applicable_stages, time.time()))
        return cur.lastrowid


def install_market_skill(user_id: int, market_id: int) -> int:
    """把市场技能装到我的用户技能（source='user'，带 market_id）。幂等：已装过返回旧 id。"""
    m = get_market_skill(market_id)
    if not m:
        raise RuntimeError("市场技能不存在")
    with _connect() as conn:
        old = conn.execute(
            "SELECT id FROM skills WHERE source='user' AND owner_id=%s AND market_id=%s",
            (user_id, market_id)).fetchone()
        if old:
            return old["id"]
        sid = create_user_skill(user_id=user_id, name=m["name"], description=m["description"],
                                prompt_append=m["prompt_append"], role=m["role"],
                                applicable_stages=m.get("applicable_stages") or "",
                                is_active=1, market_id=market_id)
        conn.execute("UPDATE skills SET downloads=downloads+1 WHERE source='market' AND id=%s",
                     (market_id,))
        return sid


def unpublish_market_skill(market_id: int, author_id: int) -> bool:
    m = get_market_skill(market_id)
    if not m or m["author_id"] != author_id:
        return False
    with _connect() as conn:
        conn.execute("DELETE FROM skills WHERE source='market' AND id=%s AND owner_id=%s",
                     (market_id, author_id))
    return True
