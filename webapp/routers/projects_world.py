# -*- coding: utf-8 -*-
"""projects_world路由（第四批拆分，从 server.py 迁移，逻辑未改动）。"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层


@app.post("/api/projects/{project_id}/cover")
def gen_cover(project_id: str, user: dict = Depends(_require_user)):
    """生成/重新生成程序化封面（PNG）。每本书最多生成 5 次。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    cfg = store.load_config()
    outline = store.load_outline()
    subtitle = (outline.intro[:18] + "…") if outline and len(outline.intro) > 18 else (outline.intro if outline else "")
    out_path, proj_path = _cover_files(store)
    # 封面次数限制：每本书最多 5 次
    COVER_MAX = 5
    count_file = proj_path.parent / "_cover_count.txt"
    used = int(count_file.read_text(encoding="utf-8").strip()) if count_file.exists() else 0
    if used >= COVER_MAX:
        raise HTTPException(400, detail=f"封面最多生成 {COVER_MAX} 次，已用完。当前封面已保留，如需调整请联系站长。")
    # 拼封面用的"书籍内容摘要"：文风 + 简介 + 世界观，传给 AI 让画面贴合小说
    cover_parts = []
    if getattr(cfg, "style", "").strip():
        cover_parts.append("文风：" + cfg.style.strip()[:80])
    if getattr(cfg, "premise", "").strip():
        cover_parts.append("故事核心：" + cfg.premise.strip()[:120])
    if getattr(cfg, "background", "").strip():
        cover_parts.append("世界观：" + cfg.background.strip()[:120])
    cover_extra = "；".join(cover_parts)
    # AI 封面优先（硅基流动 Tongyi-MAI/Z-Image-Turbo），失败自动回退程序化封面
    generate_best_cover(
        title=cfg.effective_title(),
        genre=cfg.genre,
        subtitle=subtitle,
        out_path=proj_path,
        extra=cover_extra,
    )
    # 复制到 output/ 主输出
    proj_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(proj_path.read_bytes())
    # 计数 +1
    count_file.write_text(str(used + 1), encoding="utf-8")
    return {
        "ok": True,
        "cover_url": f"/api/projects/{project_id}/cover",
        "cover_used": used + 1,
        "cover_max": COVER_MAX,
        "cover_remaining": COVER_MAX - used - 1,
    }


@app.get("/api/projects/{project_id}/cover")
def get_cover(project_id: str, token: str = "", authorization: str = Header(default="")):
    # img 标签无法带 header，支持 query token
    if authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    uid = db.get_session_user(token)
    if uid is None:
        raise HTTPException(status_code=401, detail="请先登录")
    user = db.get_user(uid)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    out_path, proj_path = _cover_files(store)
    p = out_path if out_path.exists() else proj_path
    if not p.exists():
        raise HTTPException(status_code=404, detail="封面尚未生成")
    return FileResponse(str(p), media_type="image/png")


@app.get("/api/projects/{project_id}/world")
def project_world(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    world = store.load_world()
    if not world:
        return {"world_view": "", "characters": [], "foreshadowing": []}
    return {
        "world_view": world.world_view,
        "characters": [c.to_dict() for c in world.characters],
        "foreshadowing": list(world.foreshadowing),
        "locked_settings": list(world.locked_settings),
    }


@app.put("/api/projects/{project_id}/world")
def update_world(project_id: str, payload: dict, user: dict = Depends(_require_user)):
    """用户手动编辑世界观/人物档案（增删改人物、拓展世界观文本）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    old = store.load_world() or World(world_view="", characters=[], foreshadowing=[], locked_settings=[])
    world_view = str(payload.get("world_view", "")).strip()
    chars_in = payload.get("characters", []) or []
    chars = []
    for raw in chars_in:
        if not isinstance(raw, dict):
            continue
        c = Character.from_dict(raw)
        if not c.name:
            continue
        chars.append(c)
    fs = [str(x) for x in (payload.get("foreshadowing", old.foreshadowing) or [])]
    ls = [str(x) for x in (payload.get("locked_settings", old.locked_settings) or [])]
    new_world = World(world_view=world_view, characters=chars, foreshadowing=fs, locked_settings=ls)
    store.save_world(new_world)
    logger.info("用户编辑世界观: project=%s chars=%d", project_id, len(chars))
    return {
        "world_view": new_world.world_view,
        "characters": [c.to_dict() for c in new_world.characters],
        "foreshadowing": fs,
        "locked_settings": ls,
    }


@app.get("/api/projects/{project_id}/lock")
def get_lock(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    return _load_lock(_find_project(project_id))


@app.put("/api/projects/{project_id}/lock")
def put_lock(project_id: str, payload: dict, user: dict = Depends(_require_user)):
    """切换某项锁定。body: {kind, key, locked}。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    kind = str(payload.get("kind", ""))
    key = str(payload.get("key", ""))
    locked = bool(payload.get("locked", False))
    if kind not in ("outline", "world_view", "volume", "chapter", "character"):
        raise HTTPException(status_code=400, detail="非法 kind")
    lock = _load_lock(store)
    if kind in ("outline", "world_view"):
        lock[kind] = locked
    else:
        sec = lock.get(kind, {}) or {}
        if locked:
            sec[key] = True
        else:
            sec.pop(key, None)
        lock[kind] = sec
    _save_lock(store, lock)
    logger.info("锁定切换: project=%s user=%s kind=%s key=%s locked=%s",
                project_id, user["username"], kind, key, locked)
    return lock


@app.get("/api/projects/{project_id}/research")
def project_research(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    r = store.load_research()
    if not r:
        return {}
    return r.to_dict()


@app.get("/api/projects/{project_id}/foreshadowing")
def list_foreshadowing(project_id: str, user: dict = Depends(_require_user)):
    """伏笔管理：列出世界设定中的伏笔清单。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    world = store.load_world()
    return {"foreshadowing": [str(x) for x in (world.foreshadowing if world else [])]}


@app.post("/api/projects/{project_id}/foreshadowing")
def add_foreshadowing(project_id: str, req: dict, user: dict = Depends(_require_user)):
    """添加一条伏笔。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    world = store.load_world()
    if world is None:
        raise HTTPException(status_code=400, detail="尚无世界观设定")
    item = (req.get("item") or "").strip()
    if not item:
        raise HTTPException(status_code=400, detail="伏笔内容为空")
    world.foreshadowing.append(item)
    store.save_world(world)
    return {"foreshadowing": [str(x) for x in world.foreshadowing]}


@app.delete("/api/projects/{project_id}/foreshadowing/{index}")
def delete_foreshadowing(project_id: str, index: int, user: dict = Depends(_require_user)):
    """删除一条伏笔。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    world = store.load_world()
    if world is None or index < 0 or index >= len(world.foreshadowing):
        raise HTTPException(status_code=404, detail="伏笔不存在")
    world.foreshadowing.pop(index)
    store.save_world(world)
    return {"foreshadowing": [str(x) for x in world.foreshadowing]}
