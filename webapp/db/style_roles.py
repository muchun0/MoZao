# -*- coding: utf-8 -*-
"""风格角色域数据访问：角色 CRUD、技能挂载/卸载、提示词组装、平台内置种子化。"""
from __future__ import annotations

import time
from pathlib import Path

from .base import _connect, logger
from .skills import get_skill

__all__ = [
    "list_style_roles", "get_style_role", "create_style_role", "update_style_role",
    "delete_style_role", "list_style_role_skills", "mount_skill_to_role",
    "unmount_skill_from_role", "get_style_role_assembled",
]


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
    lib_dir = Path(__file__).resolve().parent.parent.parent / "skills_library" / "style-references"
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
