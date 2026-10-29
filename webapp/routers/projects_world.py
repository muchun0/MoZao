# -*- coding: utf-8 -*-
"""projects_world路由（第四批拆分，从 server.py 迁移，逻辑未改动）。"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层
from novel_gen.pipeline.mechanism_types import (  # noqa: F401  机制类型清洗/判定（唯一事实源）
    normalize_types, resolve_types,
)


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
    return _public_world(store.load_world())


def _public_world(world) -> dict:
    """世界观编辑面板序列化（GET/PUT 共用的唯一出口，§11.3 单一事实来源）。

    REQ-通用机制类型适配：机制类型标签 `mechanism_types`、规则条款 `mechanism_ruleset`
    与既有结构化卡（金手指五要素 / 等级境界）必须一并下发，前端才能按类型分派渲染
    初始数值；旧 world 缺字段全部回落空值，不报错（from_dict 已兼容）。

    `mechanism_types_resolved` = `resolve_types(world)` 的权威结果（显式声明 → 中文别名归一 →
    旧书按字段推断 → 兜底 none）。前端一律以它分派，**不在前端重算判定口径**
    （§11.2 单一权威源；别名表只在后端一份，前端不复制，避免两处漂移）。
    """
    if world is None:
        return {
            "world_view": "", "characters": [], "foreshadowing": [], "locked_settings": [],
            "enemies": [], "locations": [], "factions": [],
            "mechanism_baseline": {}, "clock": {}, "style_taboos": [],
            "mechanism_types": [], "mechanism_types_resolved": ["none"],
            "mechanism_ruleset": [], "goldfinger_rules":
            GoldfingerRules().to_dict(), "power_system": PowerLevelSystem().to_dict(),
            "mechanism_rules": "",
        }
    return {
        "world_view": world.world_view,
        "characters": [c.to_dict() for c in world.characters],
        "foreshadowing": list(world.foreshadowing),
        "locked_settings": list(world.locked_settings),
        "enemies": [e.to_dict() for e in (getattr(world, "enemies", None) or [])],
        "locations": [l.to_dict() for l in (getattr(world, "locations", None) or [])],
        "factions": [f.to_dict() for f in (getattr(world, "factions", None) or [])],
        "mechanism_baseline": (getattr(world, "mechanism_baseline", None).to_dict()
                               if getattr(world, "mechanism_baseline", None) else {}),
        "clock": (getattr(world, "clock", None).to_dict()
                  if getattr(world, "clock", None) else {}),
        "style_taboos": list(getattr(world, "style_taboos", None) or []),
        # ---- REQ-通用机制类型适配：七类机制各自的「初始数值」承载字段 ----
        "mechanism_types": list(getattr(world, "mechanism_types", None) or []),
        "mechanism_types_resolved": resolve_types(world),
        "mechanism_ruleset": [r.to_dict() for r in (getattr(world, "mechanism_ruleset", None) or [])],
        # 缺省下发空骨架（含全部字段键），前端 v-model 绑定不会碰到 undefined
        "goldfinger_rules": (gold.to_dict()
                             if (gold := getattr(world, "goldfinger_rules", None))
                             else GoldfingerRules().to_dict()),
        "power_system": (ps.to_dict()
                         if (ps := getattr(world, "power_system", None))
                         else PowerLevelSystem().to_dict()),
        "mechanism_rules": getattr(world, "mechanism_rules", "") or "",
    }


@app.put("/api/projects/{project_id}/world")
def update_world(project_id: str, payload: dict, user: dict = Depends(_require_user)):
    """用户手动编辑世界观/人物档案/事前设计卡。

    在旧 world 上增量更新（保留 theme_proposition / conflict_matrix / protagonist_arc
    等非编辑字段），只覆盖前端传来的可编辑字段。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    old = store.load_world() or World(world_view="", characters=[])

    world_view = str(payload.get("world_view", old.world_view)).strip()
    chars = []
    for raw in (payload.get("characters", []) or []):
        if not isinstance(raw, dict):
            continue
        c = Character.from_dict(raw)
        if c.name:
            chars.append(c)

    # 事前设计卡：前端传来即覆盖，没传则保留旧值
    enemies = [EnemyCard.from_dict(e) for e in (payload.get("enemies") or getattr(old, "enemies", []))
               if isinstance(e, (dict, str))]
    locations = [LocationCard.from_dict(l) for l in (payload.get("locations") or getattr(old, "locations", []))
                 if isinstance(l, (dict, str))]
    factions = [FactionCard.from_dict(f) for f in (payload.get("factions") or getattr(old, "factions", []))
                if isinstance(f, (dict, str))]
    mb_raw = payload.get("mechanism_baseline")
    mb = MechanismBaseline.from_dict(mb_raw) if isinstance(mb_raw, dict) else getattr(old, "mechanism_baseline", None)
    ck_raw = payload.get("clock")
    ck = ClockCard.from_dict(ck_raw) if isinstance(ck_raw, dict) else getattr(old, "clock", None)
    style_taboos = [str(x) for x in (payload.get("style_taboos") or getattr(old, "style_taboos", []) or [])]

    # ---- REQ-通用机制类型适配：机制类型/条款/能力/境界体系的用户编辑 ----
    # 类型标签走 pipeline.mechanism_types.normalize_types（枚举清洗唯一事实源，§11.2）；
    # 空列表视为"未声明"而非"清空"（旧前端不带该字段时不误清，运行时按字段推断兜底）。
    if "mechanism_types" in payload:
        declared = normalize_types(payload.get("mechanism_types"))
        if declared:
            old.mechanism_types = declared
    if "mechanism_ruleset" in payload and isinstance(payload.get("mechanism_ruleset"), list):
        old.mechanism_ruleset = [
            MechanismRule.from_dict(r) for r in payload["mechanism_ruleset"]
            if isinstance(r, dict) and (r.get("statement") or "").strip()
        ]
    old.goldfinger_rules = _merge_sub_card(
        getattr(old, "goldfinger_rules", None), payload.get("goldfinger_rules"), GoldfingerRules)
    old.power_system = _merge_sub_card(
        getattr(old, "power_system", None), payload.get("power_system"), PowerLevelSystem)
    if "mechanism_rules" in payload:
        old.mechanism_rules = str(payload.get("mechanism_rules") or "").strip()

    fs = [str(x) for x in (payload.get("foreshadowing", old.foreshadowing) or [])]
    ls = [str(x) for x in (payload.get("locked_settings", old.locked_settings) or [])]

    # 复用旧对象，只更新可编辑字段，保留 theme/arc/goldfinger 等
    old.world_view = world_view
    old.characters = chars
    old.foreshadowing = fs
    old.locked_settings = ls
    old.enemies = enemies
    old.locations = locations
    old.factions = factions
    old.mechanism_baseline = mb
    old.clock = ck
    old.style_taboos = style_taboos
    store.save_world(old, commit=True, source="user")
    logger.info("用户编辑世界观: project=%s chars=%d enemies=%d locations=%d factions=%d "
                "types=%s rules=%d", project_id, len(chars), len(enemies), len(locations),
                len(factions), ",".join(getattr(old, "mechanism_types", []) or []) or "-",
                len(getattr(old, "mechanism_ruleset", []) or []))
    return _public_world(old)


def _merge_sub_card(old_card, raw, cls):
    """结构化子卡增量合并：前端没传/传空则保留旧值，避免写空壳污染既有卡。

    §11.7：空壳（字段全空）不落盘——`from_dict({})` 会产出全空对象，直接写回会让
    下游校验把"有卡但字段空"当成真实数据。
    """
    if not isinstance(raw, dict):
        return old_card
    new_card = cls.from_dict(raw)
    if not any(str(v).strip() for v in new_card.to_dict().values()
               if isinstance(v, str)) and not getattr(new_card, "levels", None):
        return old_card
    return new_card


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
    store.save_world(world, commit=True, source="user")
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
    store.save_world(world, commit=True, source="user")
    return {"foreshadowing": [str(x) for x in world.foreshadowing]}
