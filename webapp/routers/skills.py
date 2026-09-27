# -*- coding: utf-8 -*-
"""skills路由（第三批拆分，从 server.py 迁移，逻辑未改动）。"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层

@app.get("/api/skills")
def public_skills():
    """前台创建小说时用：只返回启用中的 skill。"""
    return {"skills": db.list_skills(only_active=True)}


@app.get("/api/my-skills")
def my_skills(user: dict = Depends(_require_user)):
    return {"skills": db.list_user_skills(user["id"])}


@app.post("/api/my-skills")
def create_my_skill(req: dict, user: dict = Depends(_require_user)):
    _guard_user_input(req.get("name"), "技能名称")
    _guard_user_input(req.get("prompt_append"), "技能内容")
    name = (req.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    sid = db.create_user_skill(
        user_id=user["id"],
        name=name,
        description=req.get("description", ""),
        prompt_append=req.get("prompt_append", ""),
        role=req.get("role", ""),
        is_active=1 if req.get("is_active", True) else 0,
    )
    logger.info("用户创建skill: uid=%d sid=%d name=%s", user["id"], sid, name)
    return {"ok": True, "id": sid}


@app.put("/api/my-skills/{skill_id}")
def update_my_skill(skill_id: int, req: dict, user: dict = Depends(_require_user)):
    if not db.get_user_skill(skill_id, user["id"]):
        raise HTTPException(status_code=404, detail="Skill不存在")
    fields = {}
    for k in ("name", "description", "prompt_append", "role", "is_active"):
        if k in req:
            v = req[k]
            if k == "is_active":
                v = 1 if v else 0
            fields[k] = v
    db.update_user_skill(skill_id, user["id"], **fields)
    return {"ok": True}


@app.delete("/api/my-skills/{skill_id}")
def delete_my_skill(skill_id: int, user: dict = Depends(_require_user)):
    db.delete_user_skill(skill_id, user["id"])
    return {"ok": True}


@app.get("/api/skills/market")
def skills_market_list(role: str = "", user: dict = Depends(_require_user)):
    return {"skills": db.list_market_skills(role=role, user_id=user["id"])}


@app.post("/api/skills/market")
def publish_market(req: dict, user: dict = Depends(_require_user)):
    """把我的某个 skill 发布到市场。"""
    src_id = req.get("source_id")
    if not src_id:
        raise HTTPException(status_code=400, detail="缺少 source_id")
    src = db.get_user_skill(int(src_id), user["id"])
    if not src:
        raise HTTPException(status_code=404, detail="原 skill 不存在")
    mid = db.publish_market_skill(
        author_id=user["id"],
        name=src["name"],
        description=src.get("description", ""),
        prompt_append=src["prompt_append"],
        role=src.get("role", ""),
    )
    logger.info("用户发布skill到市场: uid=%d sid=%d mid=%d name=%s",
                user["id"], src_id, mid, src["name"])
    return {"ok": True, "market_id": mid}


@app.post("/api/skills/market/{market_id}/install")
def install_market(market_id: int, user: dict = Depends(_require_user)):
    try:
        sid = db.install_market_skill(user["id"], market_id)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    logger.info("用户安装市场skill: uid=%d market_id=%d my_skill_id=%d",
                user["id"], market_id, sid)
    return {"ok": True, "user_skill_id": sid}


@app.delete("/api/skills/market/{market_id}")
def unpublish_market(market_id: int, user: dict = Depends(_require_user)):
    ok = db.unpublish_market_skill(market_id, user["id"])
    if not ok:
        raise HTTPException(status_code=403, detail="只能下架自己发布的技能")
    return {"ok": True}
