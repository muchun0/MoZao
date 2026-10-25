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


@app.post("/api/projects/{project_id}/outline/regenerate")
def regen_outline(project_id: str, user: dict = Depends(_require_user)):
    """重新生成大纲：删除旧大纲，重新跑 outline 阶段。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    _assert_not_locked(store, "outline")
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
        handle = _RunHandle()
        _RUNS[project_id] = handle
    if not _GEN_SEMAPHORE.acquire(blocking=False):
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise HTTPException(status_code=429, detail="系统繁忙")
    try:
        # 删除旧大纲文件（而非写空壳）：否则 run_outline 检测到"已有大纲对象"会直接复用，永不重新生成
        _op = store.outline_path()
        if _op.exists():
            _backup_outline(store, "重生成前")  # 覆盖前自动备份上一版（一键还原）
            _op.unlink()
        logger.info("重生成大纲启动: project=%s user=%s", project_id, user["username"])
        t = threading.Thread(target=_run_outline_only, args=(store, handle), daemon=True)
        handle.thread = t
        t.start()
    except Exception:
        logger.exception("重生成大纲启动失败: project=%s", project_id)
        _GEN_SEMAPHORE.release()
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise
    return {"started": True}


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

@app.get("/api/projects/{project_id}/outline/snapshots")
def list_outline_snapshots(project_id: str, user: dict = Depends(_require_user)):
    """列出大纲快照。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    snap_dir = store.dir / "outline_snapshots"
    if not snap_dir.exists():
        return {"snapshots": []}
    snaps = []
    for f in sorted(snap_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        import json
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            snaps.append({
                "name": f.stem,
                "display": _snap_display(f.stem),
                "created": f.stat().st_mtime,
                "chapters": len(data.get("chapters", [])),
                "title": data.get("title", ""),
            })
        except Exception:
            continue
    return {"snapshots": snaps}


@app.post("/api/projects/{project_id}/outline/snapshots")
def save_outline_snapshot(project_id: str, name: str = "", user: dict = Depends(_require_user)):
    """保存当前大纲为快照。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    outline = store.load_outline()
    if not outline:
        raise HTTPException(status_code=400, detail="还没有大纲可保存")
    import json, datetime
    snap_dir = store.dir / "outline_snapshots"
    snap_dir.mkdir(exist_ok=True)
    if not name:
        name = datetime.datetime.now().strftime("%m%d_%H%M")
    fname = f"{name}.json"
    (snap_dir / fname).write_text(json.dumps(outline.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "name": name}


@app.post("/api/projects/{project_id}/outline/snapshots/{snap_name}/restore")
def restore_outline_snapshot(project_id: str, snap_name: str, user: dict = Depends(_require_user)):
    """从快照恢复大纲。"""
    import json
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    snap_file = store.dir / "outline_snapshots" / f"{snap_name}.json"
    if not snap_file.exists():
        raise HTTPException(status_code=404, detail="快照不存在")
    data = json.loads(snap_file.read_text(encoding="utf-8"))
    from novel_gen.store.schemas import Outline
    outline = Outline.from_dict(data)
    # 快照恢复同样是用户主动选择，记为用户提交版本
    store.save_outline(outline, commit=True, source="user")
    return {"ok": True, "chapters": len(outline.chapters)}
