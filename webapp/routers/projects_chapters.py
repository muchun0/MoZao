# -*- coding: utf-8 -*-
"""projects_chapters路由（第四批拆分，从 server.py 迁移，逻辑未改动）。"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层
from novel_gen.pipeline.style_anchor import (
    PERSONAL_ANCHOR_HEADER, extract_personal_anchor, wrap_style_anchor)
from novel_gen.pipeline.templates import _style_reference


@app.post("/api/projects/{project_id}/chapters/{no}/rewrite")
def rewrite_chapter(project_id: str, no: int, user: dict = Depends(_require_user)):
    """单章重写：按当前大纲该章 goal/title 重新生成正文与摘要，后续章节不动。
    重写后该章摘要自动覆盖（append_summary 幂等）；前文记忆上下文不含本章旧摘要（memory < no）。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    outline = store.load_outline()
    if not outline or no < 1 or no > len(outline.chapters):
        raise HTTPException(status_code=400,
                            detail=f"章节号非法（大纲共 {len(outline.chapters) if outline else 0} 章）")
    if store.load_chapter(no) is None:
        raise HTTPException(status_code=400, detail=f"章节 {no} 尚未生成，无法重写")
    if store.load_chapter(no).source == "user":
        raise HTTPException(status_code=400, detail="该章为用户手写内容，AI 不自动重写；如需修改请使用「编辑本章」手动调整")
    _assert_not_locked(store, "chapter", no, "本章已被用户锁定，如需重生成请先解锁")
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
    _check_regen_limit(store, project_id)

    plan = outline.chapters[no - 1]
    try:
        pipeline = _open_pipeline(store)
        research = store.load_research()
        # 大改风控：重写时自动加载 world + memory + verify_issues 作为硬约束
        chapter = pipeline.chapter_writer.write(
            no, plan.title, plan.goal, research, is_final=(no == len(outline.chapters)))
        # 章末状态落盘（章间账本）：与写章循环一致，否则重写后账本不更新
        try:
            pipeline._update_chapter_state(no, silent=True)
        except Exception:
            logger.warning("重写后章末状态抽取失败（忽略）: no=%d", no)
    except Exception as e:
        logger.exception("单章重写失败: project=%s no=%d", project_id, no)
        raise HTTPException(status_code=500, detail=f"重写失败：{e}")

    logger.info("章节重写: uid=%d project=%s no=%d words=%d",
                user["id"], project_id, no, len(chapter.content))
    should_stop, stop_reason = _settle_incremental(store)
    # P0-3：附上该章质检结果（review.json 最新记录），前端据此显示
    # "本章质检未通过（score=0）"红标——检出≠修好也绝不再静默交付
    quality = {}
    try:
        review_path = store.dir / "review.json"
        if review_path.exists():
            import json as _json
            for r in reversed(_json.loads(review_path.read_text(encoding="utf-8"))):
                if r.get("chapter") == no:
                    qc = r.get("quality_check") or {}
                    quality = {
                        "passed": bool(qc.get("passed", True)),
                        "score": int(qc.get("score", 100)),
                        "issue_count": len(r.get("issues") or []),
                        "auto_revised": bool(r.get("auto_revised", False)),
                    }
                    break
    except Exception:
        pass
    return {"no": chapter.no, "title": chapter.title, "content": chapter.content,
            "summary": chapter.summary, "words": len(chapter.content),
            "balance_warning": stop_reason if should_stop else "",
            "quality": quality}


@app.post("/api/projects/{project_id}/expand-selection")
def expand_selection(project_id: str, req: dict, user: dict = Depends(_require_user)):
    """选中内容扩写/续写：基于用户选中的文本，AI扩写或续写一段。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    selected = (req.get("text") or "").strip()
    mode = req.get("mode") or "expand"  # expand=扩写 / continue=续写
    if not selected:
        raise HTTPException(status_code=400, detail="未提供选中文本")
    cfg = store.load_config()
    try:
        pipeline = _open_pipeline(store)
        llm = pipeline.llm
        if mode == "continue":
            sys = "你是一位网络小说写手。基于以下文字，自然续写一段，保持文风和情节连贯，100-300字。不要重复原文。"
        else:
            sys = "你是一位网络小说写手。将以下文字扩写得更细腻生动，增加感官细节和情绪，保持原意和文风，扩写后200-500字。"
        # REQ-双锚风格锚定：注入风格范文（建书时选的风格角色）+ 个人旧稿锚（用户人工编辑过的章节）。
        # 两者皆无则只保留原文（行为降级不报错）；个人锚为弱约束（只模仿语感，严禁抄袭内容）。
        ref = _style_reference(cfg)
        if ref:
            sys += "\n\n" + ref
        personal = extract_personal_anchor(store.list_chapters())
        if personal:
            sys += "\n\n" + wrap_style_anchor(PERSONAL_ANCHOR_HEADER, personal)
        user_msg = f"【原文】\n{selected}\n\n【请输出】"
        result = llm.chat(sys, user_msg, temperature=0.7, max_tokens=600)
    except Exception as e:
        logger.exception("选中扩写失败: project=%s", project_id)
        raise HTTPException(status_code=500, detail=f"扩写失败：{e}")
    return {"result": result, "mode": mode}


@app.get("/api/projects/{project_id}/stats/rhythm")
def rhythm_stats(project_id: str, user: dict = Depends(_require_user)):
    """节奏分析：每章字数分布+总进度。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    chapters = store.list_chapters()
    data = [{"no": c.no, "title": c.title, "words": len(c.content)} for c in chapters]
    total = sum(d["words"] for d in data)
    return {"chapters": data, "total_words": total, "chapter_count": len(data)}


@app.get("/api/projects/{project_id}/review")
def list_review(project_id: str, user: dict = Depends(_require_user)):
    """读取改稿记录 review.json，按章返回问题与改稿情况（P0-3：合并校验问题与全书汇总）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    path = store.dir / "review.json"
    if not path.exists():
        return {"items": [], "verify_issues": [], "summary": {}, "final_review": None}
    try:
        import json as _json
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = []
    if not isinstance(data, list):
        data = []
    data.sort(key=lambda r: int(r.get("chapter", 0)))

    # P0-3：合并每 3 章一致性校验/读者自检/编辑审稿问题 + 全书汇总
    from novel_gen.pipeline.review_summary import build_review_summary
    verify_issues = store.load_verify_issues()
    summary = build_review_summary(data, verify_issues)
    final_review = store.load_final_review()
    return {"items": data, "verify_issues": verify_issues, "summary": summary,
            "final_review": final_review}


@app.post("/api/projects/{project_id}/verify/consistency")
def verify_consistency(project_id: str, user: dict = Depends(_require_user)):
    """设定一致性校验：用LLM检查最近章节是否与世界设定矛盾。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    cfg = store.load_config()
    world = store.load_world()
    try:
        pipeline = _open_pipeline(store)
        llm = pipeline.llm
        # 读最近3章内容摘要
        summaries = store.load_summaries()[-3:]
        recent = "\n".join(f"第{s['no']}章: {s['summary']}" for s in summaries)
        world_txt = ""
        if world:
            if world.world_view:
                world_txt += f"世界观: {world.world_view}\n"
            if world.characters:
                world_txt += "人物: " + "; ".join(f"{c.name}({c.role})" for c in world.characters) + "\n"
        sys = "你是小说一致性审查员。检查以下最近章节摘要是否与世界设定矛盾，列出矛盾点（如有），JSON数组返回[{problem, suggestion}]，无矛盾返回空数组。"
        result = llm.chat(sys, f"【设定】\n{world_txt}\n【最近章节】\n{recent}", temperature=0.3, max_tokens=800)
        import json
        try:
            issues = json.loads(result)
        except Exception:
            issues = []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"校验失败：{e}")
    return {"issues": issues}


@app.post("/api/projects/{project_id}/verify/logic")
def verify_logic(project_id: str, user: dict = Depends(_require_user)):
    """剧情逻辑审查：检查因果链是否断裂。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    try:
        pipeline = _open_pipeline(store)
        llm = pipeline.llm
        summaries = store.load_summaries()[-5:]
        recent = "\n".join(f"第{s['no']}章: {s['summary']}" for s in summaries)
        sys = "你是小说逻辑审查员。检查以下章节摘要的剧情因果是否合理，列出逻辑漏洞（如有），JSON数组返回[{problem, suggestion}]，无问题返回空数组。"
        result = llm.chat(sys, f"【章节摘要】\n{recent}", temperature=0.3, max_tokens=800)
        import json
        try:
            issues = json.loads(result)
        except Exception:
            issues = []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"审查失败：{e}")
    return {"issues": issues}


@app.post("/api/projects/{project_id}/verify/compliance")
def verify_compliance(project_id: str, user: dict = Depends(_require_user)):
    """网文合规检测：检查敏感/违规内容。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    try:
        pipeline = _open_pipeline(store)
        llm = pipeline.llm
        summaries = store.load_summaries()[-3:]
        recent = "\n".join(f"第{s['no']}章: {s['summary']}" for s in summaries)
        sys = "你是网文合规审核员。检查以下内容是否涉及政治敏感、色情低俗、暴力血腥、违法犯罪描写，列出风险点JSON数组[{issue, level}]，无风险返回空数组。"
        result = llm.chat(sys, f"【章节摘要】\n{recent}", temperature=0.2, max_tokens=600)
        import json
        try:
            issues = json.loads(result)
        except Exception:
            issues = []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"检测失败：{e}")
    return {"issues": issues}


@app.post("/api/projects/{project_id}/generate/alternatives")
def generate_alternatives(project_id: str, req: dict, user: dict = Depends(_require_user)):
    """多方案生成：给定章节目标，生成2-3个不同剧情走向方案。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    goal = (req.get("goal") or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="缺少章节目标")
    try:
        pipeline = _open_pipeline(store)
        llm = pipeline.llm
        sys = "你是小说剧情策划。针对给定章节目标，提供3个不同的剧情走向方案（不同选择/冲突/转折），每个方案2-3句话。JSON数组返回[{title, description}]。"
        result = llm.chat(sys, f"【章节目标】{goal}", temperature=0.8, max_tokens=800)
        import json
        try:
            alts = json.loads(result)
        except Exception:
            alts = []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成失败：{e}")
    return {"alternatives": alts}


@app.get("/api/projects/{project_id}/chapters")
def project_chapters(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    chapters = store.list_chapters()
    return {
        "chapters": [
            {"no": c.no, "title": c.title, "words": len(c.content), "summary": c.summary}
            for c in chapters
        ]
    }


@app.get("/api/projects/{project_id}/chapters/{no}")
def project_chapter(project_id: str, no: int, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    ch = store.load_chapter(no)
    if not ch:
        raise HTTPException(status_code=404, detail=f"章节 {no} 未生成")
    return {"no": ch.no, "title": ch.title, "content": ch.content, "summary": ch.summary}


@app.put("/api/projects/{project_id}/chapters/{no}")
def edit_chapter(project_id: str, no: int, req: ChapterEditReq,
                 user: dict = Depends(_require_user)):
    """编辑单章：改标题/正文，可选重新生成摘要，供「人工修改后继续生成」使用。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    ch = store.load_chapter(no)
    if not ch:
        raise HTTPException(status_code=404, detail=f"章节 {no} 未生成")
    _guard_user_input(req.title, "章节标题")
    _guard_user_input(req.content, "章节正文")

    if req.title:
        ch.title = req.title
    if req.content:
        ch.content = req.content

    # 摘要处理：用户显式给 → 用之；否则若正文变了且允许 → 自动重新生成
    if req.summary:
        ch.summary = req.summary
    elif req.regen_summary and req.content and req.content != _load_prev_content(store, no):
        new_summary = _regenerate_summary(store, ch)
        if new_summary:
            ch.summary = new_summary

    # REQ-双锚风格锚定：用户编辑保存 = 人工修订标记（个人风格锚来源）
    ch.user_edited = True
    store.save_chapter(ch)
    if ch.summary:
        store.append_summary(no, ch.summary)
    logger.info("章节编辑: uid=%d project=%s no=%d title=%s words=%d",
                user["id"], project_id, no, ch.title, len(ch.content))
    return {"no": ch.no, "title": ch.title, "summary": ch.summary, "words": len(ch.content)}


@app.post("/api/projects/{project_id}/regenerate-from/{no}")
def regenerate_from(project_id: str, no: int, user: dict = Depends(_require_user)):
    """从第 no 章重新生成：删除 no 及之后所有章节与记忆，然后从 no 重新开跑。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    outline = store.load_outline()
    if not outline or no < 1 or no > len(outline.chapters):
        raise HTTPException(status_code=400, detail=f"章节号非法（大纲共 {len(outline.chapters) if outline else 0} 章）")
    user_max = max((c.no for c in store.list_chapters() if c.source == "user"), default=0)
    if user_max and no <= user_max:
        raise HTTPException(status_code=400,
                            detail=f"第 {no} 章为用户手写内容（到第 {user_max} 章），重新生成会覆盖手写内容，已禁止；"
                                   f"请从第 {user_max + 1} 章开始")
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
        handle = _RunHandle()
        _RUNS[project_id] = handle
    _check_regen_limit(store, project_id)
    # P1 修复：原路径只 release 不 acquire，导致信号量永久泄漏。这里补 acquire。
    if not _GEN_SEMAPHORE.acquire(blocking=False):
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise HTTPException(status_code=429, detail="系统繁忙，已有多个任务在生成，请稍后再试")
    try:
        # 删除 no 及之后章节 + 对应记忆
        for ch in store.list_chapters():
            if ch.no >= no:
                p = store.chapter_path(ch.no)
                if p.exists():
                    p.unlink()
        summaries = [x for x in store.load_summaries() if int(x.get("no", 0)) < no]
        store._write_json(store.memory_path(), summaries)

        logger.info("章节重生成: uid=%d project=%s from_chapter=%d", user["id"], project_id, no)
        t = threading.Thread(target=_run_generation, args=(store, handle, no), daemon=True)
        handle.thread = t
        t.start()
    except Exception:
        _GEN_SEMAPHORE.release()
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise
    return {"started": True, "project_id": project_id, "from_chapter": no}


@app.post("/api/projects/{project_id}/chapters/batch")
def batch_generate(project_id: str, req: dict, user: dict = Depends(_require_user)):
    """批量生成章节：指定起始章和结束章，后台逐章生成。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    start = int(req.get("start", 1))
    end = int(req.get("end", start))
    if start < 1 or end < start:
        raise HTTPException(status_code=400, detail="章节范围非法")
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
    if not _GEN_SEMAPHORE.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="系统繁忙，已有多个任务在生成，请稍后再试")
    # P1 修复：用真 _RunHandle（含 stop_event/error），finally 清理 _RUNS，结束后结算
    handle = _RunHandle()
    t = threading.Thread(target=_run_batch, args=(store, handle, start, end), daemon=True)
    handle.thread = t
    _RUNS[project_id] = handle
    t.start()
    return {"msg": f"批量生成已启动：第{start}-{end}章"}
