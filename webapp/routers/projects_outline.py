# -*- coding: utf-8 -*-
"""projects_outline路由（第四批拆分，从 server.py 迁移，逻辑未改动）。"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层


@app.get("/api/projects/{project_id}/outline")
def project_outline(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    outline = store.load_outline()
    if not outline:
        return {"chapters": []}
    return {
        "title": outline.title,
        "intro": outline.intro,
        "core_conflict": outline.core_conflict,
        "main_line": outline.main_line,
        "timelines": outline.timelines,
        "units": outline.units,
        "volumes": [v.to_dict() if hasattr(v, "to_dict") else v for v in (outline.volumes or [])],
        "chapters": [c.to_dict() for c in outline.chapters],
    }


@app.put("/api/projects/{project_id}/outline")
def save_outline(project_id: str, req: OutlineSaveReq, user: dict = Depends(_require_user)):
    """保存大纲（章节级编辑：改标题/目标、增删章）。
    不自动触发重生成；返回与已生成章节的差异，由用户显式选择重写动作。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中，请先停止再编辑大纲")
    if not req.chapters:
        raise HTTPException(status_code=400, detail="大纲至少需要 1 个章节")

    # 规范化：重排 no、空 title/goal 补默认（与生成端 OutlineGenerator._normalize 一致）
    # 分卷与章节卷号：完整接收并落盘（volume_no 支持前端拖拽/手动调整后持久化）
    chapters = []
    for i, c in enumerate(req.chapters, start=1):
        title = (c.title or "").strip() or f"第{i}章"
        goal = (c.goal or "").strip() or "推进主线剧情，制造冲突并留下悬念。"
        chapters.append(ChapterPlan(
            no=i, title=title, goal=goal,
            core_event=(c.core_event or "").strip(),
            conflict=(c.conflict or "").strip(),
            character_moves=(c.character_moves or "").strip(),
            foreshadowing=(c.foreshadowing or "").strip(),
            emotion=(c.emotion or "").strip(),
            transition=(c.transition or "").strip(),
            tag=(c.tag or "").strip(),
            volume_no=int(getattr(c, "volume_no", 0) or 0),
        ))
    unique_chapter_titles(chapters)  # 章节名全书唯一（重名自动加后缀）

    old = store.load_outline()
    # 分卷数组：来自前端（含增删卷），空则回退保留旧值（兼容旧前端不传 volumes）
    if req.volumes:
        from novel_gen.store.schemas import Volume
        volumes = [
            Volume(no=int(v.no or 0), name=(v.name or ""), intro=(v.intro or ""),
                   main_conflict=(v.main_conflict or ""), character_growth=(v.character_growth or ""),
                   climax=(v.climax or ""), start_ch=int(v.start_ch or 0), end_ch=int(v.end_ch or 0))
            for v in req.volumes
        ]
    else:
        volumes = list((old.volumes or [])) if old else []

    new_outline = Outline(
        title=(req.title or "").strip() or (old.title if old else ""),
        intro=(req.intro or "").strip() or (old.intro if old else ""),
        volumes=volumes,
        chapters=chapters,
    )
    # 用户编辑大纲 = 一次用户提交（source="user" 置 user_managed；版本化关闭时只写 HEAD）
    store.save_outline(new_outline, commit=True, source="user")

    changed, removed = _outline_diff(new_outline, store)
    logger.info("大纲编辑: uid=%d project=%s chapters=%d→%d changed=%s removed=%s",
                user["id"], project_id, len(old.chapters) if old else 0, len(chapters), changed, removed)
    return {"saved": True, "total_chapters": len(chapters),
            "changed_chapters": changed, "removed_chapters": removed}


@app.get("/api/projects/{project_id}/outline/diff")
def outline_diff(project_id: str, user: dict = Depends(_require_user)):
    """大纲与已生成章节的差异（前端章节列表标记"与新大纲不一致"）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    outline = store.load_outline()
    if not outline:
        return {"changed": [], "removed": []}
    changed, removed = _outline_diff(outline, store)
    return {"changed": changed, "removed": removed}



def _snap_display(name: str) -> str:
    """把快照文件名转成可读时间文案：
    snap_<epoch>（系统自动）→ 自动备份 MM-DD HH:MM:SS；
    导入前_MMDD_HHMMSS / 重生成前_MMDD_HHMMSS（覆盖前自动备份）→ 前缀 + MM-DD HH:MM:SS；
    手动命名的快照原样返回。
    """
    import datetime
    import re as _re
    m = _re.match(r"^snap_(\d+)$", name)
    if m:
        try:
            dt = datetime.datetime.fromtimestamp(int(m.group(1)))
            return "自动备份 " + dt.strftime("%m-%d %H:%M:%S")
        except Exception:
            return name
    m = _re.match(r"^([\u4e00-\u9fa5]+)_(\d{4})_(\d{6})(?:_(\d+))?$", name)
    if m:
        try:
            prefix, mmdd, hms = m.group(1), m.group(2), m.group(3)
            return f"{prefix} {mmdd[:2]}-{mmdd[2:]} {hms[:2]}:{hms[2:4]}:{hms[4:]}"
        except Exception:
            return name
    return name


# REQ-R23 P1-1：大纲快照并入版本库。
# 快照目录（outline_snapshots/*.json）是版本化门面之前的旧产物，关闭开关时才会继续写；
# 这里做一次性只读导入：把存量快照写进 outline 版本库并打标签，之后界面只在版本弹窗里看。
_SNAP_MARKER = ".r23_merged.json"


def _import_legacy_snapshots(store) -> int:
    """把 outline_snapshots/ 存量快照导入 outline 版本库（幂等）。

    - 按 mtime 升序导入，版本时间沿用文件时间，保证版本弹窗里的先后顺序与真实历史一致；
    - source='user'、promote=False（只存档，不改当前生效大纲）；
    - 已导入过的文件名记在 `.r23_merged.json`，重跑零差异；内容重复时 commit 自身幂等；
    - 原始文件**不删除**（用户数据，保留可回溯），只是不再作为独立入口展示。
    """
    from novel_gen.store.versioned import VersionedProduct
    snap_dir = store.dir / "outline_snapshots"
    if not snap_dir.is_dir() or not store.versioned_enabled():
        return 0
    import json
    marker = snap_dir / _SNAP_MARKER
    merged: set[str] = set()
    if marker.exists():
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            merged = {str(x) for x in (data.get("merged") or [])}
        except Exception:
            merged = set()
    files = [p for p in snap_dir.glob("*.json") if p.name != _SNAP_MARKER]
    files.sort(key=lambda p: p.stat().st_mtime)
    vp = VersionedProduct(store, "outline")
    imported = 0
    for p in files:
        if p.stem in merged:
            continue
        try:
            content = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("快照读取失败，跳过: project=%s file=%s", store.dir.name, p.name)
            continue
        label = "快照 " + _snap_display(p.stem)
        try:
            vp.commit("all", content, source="user", promote=False,
                      auto_select_best=False, label=label, ts=int(p.stat().st_mtime))
        except Exception:
            logger.exception("快照并入版本失败: project=%s file=%s", store.dir.name, p.name)
            continue
        merged.add(p.stem)
        imported += 1
    if imported:
        store._write_json(marker, {"merged": sorted(merged)})
    return imported


@app.get("/api/projects/{project_id}/outline/snapshots")
def list_outline_snapshots(project_id: str, user: dict = Depends(_require_user)):
    """列出大纲历史保存点（REQ-R23 P1-1：已并入版本库，这里是只读兼容视图）。

    返回带「快照 / 备份」标签的 outline 版本，name 用版本标签；
    界面唯一入口是版本弹窗（products/outline/versions），本接口不再驱动新的 UI 入口。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    _import_legacy_snapshots(store)
    from novel_gen.store.versioned import VersionedProduct
    snaps = []
    if store.versioned_enabled():
        for item in VersionedProduct(store, "outline").list("all"):
            label = item.get("label") or ""
            if not label:
                continue
            data = VersionedProduct(store, "outline").load_version("all", item["v"]) or {}
            snaps.append({
                "name": label, "display": label, "v": item["v"],
                "created": item.get("ts") or 0,
                "chapters": len(data.get("chapters", []) or []),
                "title": data.get("title", "") or "",
            })
    snaps.sort(key=lambda x: x["created"], reverse=True)
    return {"snapshots": snaps}


@app.post("/api/projects/{project_id}/outline/snapshots")
def save_outline_snapshot(project_id: str, name: str = "", user: dict = Depends(_require_user)):
    """保存当前大纲为一个历史保存点（REQ-R23 P1-1：写成 outline 版本，不再落独立快照文件）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    outline = store.load_outline()
    if not outline:
        raise HTTPException(status_code=400, detail="还没有大纲可保存")
    if not store.versioned_enabled():
        raise HTTPException(status_code=409, detail="版本库未启用，暂不能保存历史版本")
    import datetime
    from novel_gen.store.versioned import VersionedProduct
    label = f"快照 {' '.join(filter(None, [name.strip(), datetime.datetime.now().strftime('%m-%d %H:%M')]))}"
    v = VersionedProduct(store, "outline").commit(
        "all", outline.to_dict(), source="user", promote=False,
        auto_select_best=False, label=label)
    if v is None:
        return {"ok": True, "v": None, "unchanged": True, "name": label}
    return {"ok": True, "v": v, "name": label}


@app.post("/api/projects/{project_id}/outline/snapshots/{snap_name}/restore")
def restore_outline_snapshot(project_id: str, snap_name: str, user: dict = Depends(_require_user)):
    """还原到某个大纲版本（REQ-R23 P1-1：按版本标签找回并切换为当前生效大纲）。

    与版本弹窗的「采用此版本」同一语义：只切 HEAD，不改写已生成正文。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    if not store.versioned_enabled():
        raise HTTPException(status_code=409, detail="版本库未启用，请用版本弹窗的旧快照文件还原")
    _import_legacy_snapshots(store)
    from novel_gen.store.versioned import VersionedProduct
    vp = VersionedProduct(store, "outline")
    target = None
    for item in vp.list("all"):
        if item.get("v") == snap_name or (item.get("label") or "") == snap_name:
            target = item["v"]
            break
    if target is None:
        raise HTTPException(status_code=404, detail="该版本不存在（可能已被版本上限淘汰）")
    data = vp.select("all", target)
    if data is None:
        raise HTTPException(status_code=404, detail="该版本内容已丢失")
    return {"ok": True, "v": target, "chapters": len(data.get("chapters", []) or [])}
