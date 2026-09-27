# -*- coding: utf-8 -*-
"""写作风格角色（Style Role）路由：把写作风格抽象为 agent 角色，可挂载技能。

用户级（登录即可）：
- GET    /api/style-roles                   我的 + 平台内置风格角色（建书时选）
- POST   /api/style-roles                   创建我的风格角色
- PUT    /api/style-roles/{id}              更新我的风格角色（管理员可改平台角色）
- DELETE /api/style-roles/{id}              删除我的风格角色（管理员可删平台角色）
- POST   /api/style-roles/{id}/skills       挂载技能（admin=平台写作技能 / user=我的技能）
- DELETE /api/style-roles/{id}/skills/{skill_type}/{skill_id}  卸载技能

管理员级：
- GET    /api/admin/style-roles             全部风格角色
"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层


def _can_manage(role: dict | None, user: dict) -> bool:
    """可管理 = 管理员 或 该风格角色的创建者。"""
    return bool(role) and (user.get("is_admin") or role.get("user_id") == user["id"])


# ---------- 用户级 ----------

@app.get("/api/style-roles")
def list_style_roles(user: dict = Depends(_require_user)):
    """建书用：平台内置（启用）+ 我自建的（启用）风格角色。"""
    return {"roles": db.list_style_roles(only_active=True, owner_id=user["id"])}


@app.post("/api/style-roles", status_code=201)
def create_style_role(req: StyleRoleReq, user: dict = Depends(_require_user)):
    _guard_user_input(req.name, "风格角色名称")
    _guard_user_input(req.role_prompt, "风格角色人设")
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    rid = db.create_style_role(
        user_id=user["id"],
        name=name[:80],
        description=(req.description or "")[:255],
        role_prompt=req.role_prompt or "",
        is_active=1 if req.is_active else 0,
        sort_order=req.sort_order,
    )
    logger.info("创建风格角色: uid=%d rid=%d name=%s", user["id"], rid, name)
    return {"ok": True, "id": rid}


@app.put("/api/style-roles/{role_id}")
def update_style_role(role_id: int, req: StyleRoleReq, user: dict = Depends(_require_user)):
    role = db.get_style_role(role_id)
    if not _can_manage(role, user):
        raise HTTPException(status_code=403, detail="只能编辑自己创建的风格角色")
    fields = {}
    if req.name is not None:
        fields["name"] = (req.name or "").strip()[:80]
    if req.description is not None:
        fields["description"] = (req.description or "")[:255]
    if req.role_prompt is not None:
        fields["role_prompt"] = req.role_prompt or ""
    if req.is_active is not None:
        fields["is_active"] = 1 if req.is_active else 0
    if req.sort_order is not None:
        fields["sort_order"] = req.sort_order
    if not fields.get("name"):
        raise HTTPException(status_code=400, detail="名称不能为空")
    db.update_style_role(role_id, **fields)
    logger.info("更新风格角色: rid=%d by=%s", role_id, user["username"])
    return {"ok": True}


@app.delete("/api/style-roles/{role_id}")
def delete_style_role(role_id: int, user: dict = Depends(_require_user)):
    role = db.get_style_role(role_id)
    if not _can_manage(role, user):
        raise HTTPException(status_code=403, detail="只能删除自己创建的风格角色")
    db.delete_style_role(role_id)
    logger.info("删除风格角色: rid=%d by=%s", role_id, user["username"])
    return {"ok": True}


@app.post("/api/style-roles/{role_id}/skills")
def mount_skill(role_id: int, req: StyleRoleSkillReq, user: dict = Depends(_require_user)):
    role = db.get_style_role(role_id)
    if not _can_manage(role, user):
        raise HTTPException(status_code=403, detail="只能给自己创建的风格角色挂载技能")
    try:
        db.mount_skill_to_role(role_id, req.skill_type, req.skill_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("挂载技能到风格角色: rid=%d type=%s sid=%d by=%s",
                role_id, req.skill_type, req.skill_id, user["username"])
    return {"ok": True}


@app.delete("/api/style-roles/{role_id}/skills/{skill_type}/{skill_id}")
def unmount_skill(role_id: int, skill_type: str, skill_id: int,
                  user: dict = Depends(_require_user)):
    role = db.get_style_role(role_id)
    if not _can_manage(role, user):
        raise HTTPException(status_code=403, detail="只能给自己创建的风格角色卸载技能")
    db.unmount_skill_from_role(role_id, skill_type, skill_id)
    logger.info("卸载技能: rid=%d type=%s sid=%d by=%s",
                role_id, skill_type, skill_id, user["username"])
    return {"ok": True}


# ---------- 管理员级 ----------

@app.get("/api/admin/style-roles")
def admin_list_style_roles(user: dict = Depends(_require_admin)):
    return {"roles": db.list_style_roles(owner_id=None)}
