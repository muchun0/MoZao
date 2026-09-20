# -*- coding: utf-8 -*-
"""projects路由（第三批拆分，从 server.py 迁移，逻辑未改动）。"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层

@app.get("/api/projects")
def projects(user: dict = Depends(_require_user), sort: str = "created", show_archived: bool = False):
    return {"projects": _list_projects(user, sort=sort, show_archived=show_archived)}


@app.post("/api/projects", status_code=201)
def create_project(req: ProjectCreateReq, user: dict = Depends(_require_user)):
    cfg = NovelConfig(
        title=req.title,
        genre=req.genre,
        total_words=req.total_words,
        chapter_words=req.chapter_words,
        style=req.style,
        premise=req.premise,
        background=req.background,
        hero=req.hero,
        characters=list(req.characters),
        plot_direction=req.plot_direction,
        forbidden=list(req.forbidden),
        editor_review=req.editor_review,
        de_ai=req.de_ai,
        anti_similarity=req.anti_similarity,
        character_depth=req.character_depth,
        confirm=req.confirm,
        memory_window=req.memory_window,
        mode=req.mode,
        expand_input=req.expand_input,
        content_type=req.content_type,
        expand_targets=list(req.expand_targets),
        llm=_resolve_llm(req.llm.provider, req.llm.model, req.llm.base_url,
                         req.llm.temperature, req.llm.top_p, req.llm.max_tokens),
        search=SearchConfig(
            provider=req.search.provider,
            api_key=_server_api_key(req.search.provider)
            if req.search.provider else "",
            count=5,
        ),
        pacing_density=req.pacing_density,
        plot_speed=req.plot_speed,
        custom_prompt=req.custom_prompt,
        high_quality_mode=req.high_quality_mode,
    )

    # 注入写作风格 skill
    if req.skill_id:
        _sk = db.get_skill(req.skill_id)
        if _sk and _sk["is_active"]:
            cfg.skill_prompt_append = _sk["prompt_append"] or ""

    # 配置校验（非硬约束问题仅警告）
    issues = validate_config(cfg)
    real_issues = [i for i in issues if "硬约束" not in i]

    # 扩写模式校验：纯扩写需大纲/简要内容；续写/扩写已有章节需已有正文
    user_content = (req.user_content or "").strip()
    if req.mode == "expand":
        if req.content_type in ("chapters", "chapters_expand"):
            if not user_content:
                raise HTTPException(status_code=400, detail="续写/扩写模式必须提供已有内容（粘贴或上传）")
        elif not req.expand_input.strip():
            raise HTTPException(status_code=400, detail="AI 扩写模式必须填写『大纲或简要内容』")

    # 续写/扩写已有章节：先切分用户内容，确定 AI 需生成的字数（已有内容不收费）
    user_chapters = []
    user_words = 0
    if req.mode == "expand" and req.content_type in ("chapters", "chapters_expand"):
        user_chapters = split_chapters(user_content)
        if not user_chapters:
            raise HTTPException(status_code=400, detail="无法从提供的内容中识别出章节，请检查文本格式")
        user_words = sum(len(c["content"]) for c in user_chapters)
        if req.total_words <= user_words:
            raise HTTPException(
                status_code=400,
                detail=f"目标字数（{req.total_words}）需大于已有内容字数（{user_words}），"
                       f"请调高目标字数或减少已有内容",
            )

    # 按 AI 实际生成字数预估费用：创建时【不扣费】，仅做余额校验（防生成后付不起），
    # 生成完成后按实际 AI 字数结算扣费（见 _settle_price）
    ai_words = max(1, cfg.total_words - user_words)
    base_price = price_for(ai_words, high_quality=bool(cfg.__dict__.get('high_quality_mode', False)))
    discount = float(user.get("agent_discount", 1.0)) if user.get("is_agent") else 1.0
    price = agent_price(base_price, discount)  # 预估费用（展示用）
    if user["balance"] < price:
        logger.warning("创建项目失败(余额不足): uid=%d title=%s price=%.2f balance=%.2f",
                       user["id"], cfg.effective_title(), price, user["balance"])
        raise HTTPException(
            status_code=400,
            detail=f"余额不足：预估费用 {price:.1f} 元（按 AI 生成 {ai_words} 字"
                   f"{'，代理价' + str(int(discount * 100)) + ' 折' if discount < 1 else ''}，"
                   f"生成完成后扣费），当前余额 {user['balance']:.1f} 元。请先充值。",
        )

    store = ProjectStore.create(PROJECTS_DIR, cfg)
    # 写入用户手写章节（source=user，AI 流程不覆盖）
    if user_chapters:
        store.save_user_content(user_content)
        for i, c in enumerate(user_chapters, start=1):
            store.save_chapter(Chapter(no=i, title=c["title"] or f"第{i}章",
                                       content=c["content"], source="user"))
        store.set_user_words(user_words)
    # 创建仅登记归属：price=0（未扣费），折扣快照留待结算
    db.claim_project(store.dir.name, user["id"], 0.0, discount)
    logger.info("创建项目(未扣费): uid=%d project=%s title=%s mode=%s content_type=%s "
                "预估=%.2f(原价%.2f) total_words=%d user_words=%d ai_words=%d",
                user["id"], store.dir.name, cfg.effective_title(), cfg.mode, cfg.content_type,
                price, base_price, cfg.total_words, user_words, ai_words)
    return {
        "id": store.dir.name,
        "title": cfg.effective_title(),
        "price": price,          # 预估费用（生成完成后按实际结算）
        "base_price": base_price,
        "balance": db.get_user(user["id"])["balance"],
        "config": _cfg_to_dict(cfg),
        "warnings": real_issues,
    }


@app.get("/api/projects/{project_id}")
def project_detail(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    cfg = store.load_config()
    state = store.load_state()
    outline = store.load_outline()
    chapters = store.list_chapters()
    total = len(outline.chapters) if outline else cfg.num_chapters
    handle = _RUNS.get(project_id)
    return {
        "id": store.dir.name,
        "config": _cfg_to_dict(cfg),
        "state": state.to_dict(),
        "outline_title": outline.title if outline else "",
        "has_outline": bool(outline and outline.chapters),
        "outline_intro": outline.intro if outline else "",
        "chapters_done": len(chapters),
        "chapters_total": total,
        "running": bool(handle and handle.thread and handle.thread.is_alive()),
        "error": (handle.error if handle and handle.error else state.error),
        "generated_words": sum(len(c.content) for c in chapters),
        "has_txt": (OUTPUT_DIR / f"{_safe_name(cfg.effective_title())}.txt").exists(),
        "has_cover": _cover_files(store)[0].exists(),
        "cover_used": _cover_used_count(store),
        "cover_max": 5,
        "has_epub": (OUTPUT_DIR / f"{_safe_name(cfg.effective_title())}.epub").exists(),
        "has_docx": (OUTPUT_DIR / f"{_safe_name(cfg.effective_title())}.docx").exists(),
    }


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
        ))
    unique_chapter_titles(chapters)  # 章节名全书唯一（重名自动加后缀）

    old = store.load_outline()
    new_outline = Outline(
        title=(req.title or "").strip() or (old.title if old else ""),
        intro=(req.intro or "").strip() or (old.intro if old else ""),
        chapters=chapters,
    )
    store.save_outline(new_outline)

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
    except Exception as e:
        logger.exception("单章重写失败: project=%s no=%d", project_id, no)
        raise HTTPException(status_code=500, detail=f"重写失败：{e}")

    logger.info("章节重写: uid=%d project=%s no=%d words=%d",
                user["id"], project_id, no, len(chapter.content))
    should_stop, stop_reason = _settle_incremental(store)
    return {"no": chapter.no, "title": chapter.title, "content": chapter.content,
            "summary": chapter.summary, "words": len(chapter.content),
            "balance_warning": stop_reason if should_stop else ""}


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
    """读取改稿记录 review.json，按章返回问题与改稿情况。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    path = store.dir / "review.json"
    if not path.exists():
        return {"items": []}
    try:
        import json as _json
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = []
    data.sort(key=lambda r: int(r.get("chapter", 0)))
    return {"items": data}


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
    # AI 封面优先（硅基流动 ERNIE-Image-Turbo），失败自动回退程序化封面
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


@app.post("/api/projects/{project_id}/volumes/{vol_no}/regenerate")
def regenerate_volume(project_id: str, vol_no: int, user: dict = Depends(_require_user)):
    """单独重生成某分卷：只重写该分卷的 intro 和其下章节的 title/goal，其他分卷不动。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    _assert_not_locked(store, "volume", vol_no, "该分卷已被锁定，如需重生成请先解锁")
    outline = store.load_outline()
    if not outline or not outline.volumes:
        raise HTTPException(status_code=400, detail="尚无大纲")
    vol = next((v for v in outline.volumes if v.no == vol_no), None)
    if vol is None:
        raise HTTPException(status_code=404, detail="分卷不存在")
    targets = [c for c in outline.chapters if c.volume_no == vol_no]
    if not targets:
        raise HTTPException(status_code=400, detail="该分卷下暂无章节")
    cfg = store.load_config()
    try:
        pipeline = _open_pipeline(store)
        llm = pipeline.llm
        sys = ("你是资深网文大纲策划。根据总纲重写指定分卷的简介和该卷章节规划，"
               "保持与其他分卷的衔接，不要改动其他分卷。只输出JSON。")
        user_msg = (
            f"书名：{outline.title}\n题材：{cfg.genre}\n总纲：{outline.intro}\n"
            f"要重写的分卷：{vol.name}（当前简介：{vol.intro}）\n"
            f"该卷共 {len(targets)} 章。请输出JSON："
            '{"intro":"分卷简介","chapters":[{"title":"章名","goal":"本章目标"}...]}，'
            f"chapters 数量必须为 {len(targets)}，顺序对应。")
        raw = llm.chat(sys, user_msg, temperature=0.7, max_tokens=2000)
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        vol.intro = str(data.get("intro", vol.intro))
        new_chs = data.get("chapters", [])
        for i, ch in enumerate(targets):
            if i < len(new_chs):
                ch.title = str(new_chs[i].get("title", ch.title))
                ch.goal = str(new_chs[i].get("goal", ch.goal))
        store.save_outline(outline)
        logger.info("分卷重生成: project=%s vol=%d", project_id, vol_no)
        return {"ok": True, "intro": vol.intro, "chapters": [{"no": c.no, "title": c.title, "goal": c.goal} for c in targets]}
    except Exception as e:
        logger.exception("分卷重生成失败: project=%s vol=%d", project_id, vol_no)
        raise HTTPException(status_code=500, detail=f"重生成失败：{e}")


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


@app.post("/api/projects/{project_id}/generate-outline")
def generate_outline_only(project_id: str, user: dict = Depends(_require_user)):
    """只跑 research → outline → editor_review → world，不写正文。
    生成完后用户可以编辑大纲，再调 /start 开始写正文。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
        handle = _RunHandle()
        _RUNS[project_id] = handle
    if not _GEN_SEMAPHORE.acquire(blocking=False):
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise HTTPException(status_code=429, detail="系统繁忙，已有多个任务在生成，请稍后再试")
    try:
        t = threading.Thread(target=_run_outline_only, args=(store, handle), daemon=True)
        handle.thread = t
        t.start()
    except Exception:
        _GEN_SEMAPHORE.release()
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise
    return {"started": True, "project_id": project_id, "stage": "outline"}


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


@app.post("/api/projects/{project_id}/world/regenerate")
def regen_world(project_id: str, user: dict = Depends(_require_user)):
    """重新生成世界观。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    _assert_not_locked(store, "world_view")
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
        def _do():
            try:
                pipeline = _open_pipeline(store)
                from novel_gen.store.schemas import World
                store.save_world(World(world_view="", characters=[], foreshadowing=[]))
                pipeline.run_world()
            except Exception as e:
                handle.error = str(e)
            finally:
                _GEN_SEMAPHORE.release()
                with _LOCK:
                    _RUNS.pop(project_id, None)
        logger.info("重生成世界观启动: project=%s user=%s", project_id, user["username"])
        t = threading.Thread(target=_do, daemon=True)
        handle.thread = t
        t.start()
    except Exception:
        logger.exception("重生成世界观启动失败: project=%s", project_id)
        _GEN_SEMAPHORE.release()
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise
    return {"started": True}


@app.post("/api/projects/{project_id}/start")
def start_generation(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    handle = _acquire_slot(project_id)
    try:
        t = threading.Thread(target=_run_generation, args=(store, handle), daemon=True)
        handle.thread = t
        t.start()
    except Exception:
        _release_slot(project_id)
        raise
    return {"started": True, "project_id": project_id}


@app.post("/api/projects/{project_id}/stop")
def stop_generation(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    handle = _RUNS.get(project_id)
    if not handle:
        return {"stopped": False, "message": "项目未在运行"}
    handle.stop_event.set()
    return {"stopped": True, "project_id": project_id, "message": "已请求停止，当前章节完成后生效"}


@app.post("/api/projects/{project_id}/refund")
def refund_project(project_id: str, user: dict = Depends(_require_user)):
    """未开始生成（零章节产出）的项目：全额退费 + 删除作品。

    退费条件：项目归属当前用户、尚未生成任何章节正文、创建时确有扣费。
    退费时同步冲正上级消费返佣（防刷量），然后删除项目目录与归属记录。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    title = store.load_config().effective_title()

    if store.list_chapters():
        raise HTTPException(status_code=400, detail="该项目已开始生成正文，无法退费删除")

    price = db.project_price(project_id)  # 新项目创建不扣费 price=0：未生成直接删除，无需退费
    # 若正在生成（调研/大纲/设定阶段，尚未产出章节）：先请求停止并等待线程结束
    handle = _RUNS.get(project_id)
    if handle:
        handle.stop_event.set()
        if handle.thread and handle.thread.is_alive():
            handle.thread.join(timeout=30)

    # 退费（历史项目创建时已扣费才退；新项目 price=0 直接删除）
    if price > 0:
        db.change_balance(user["id"], price, f"项目退费：《{title}》（未开始生成）")
        # 返佣冲正：该项目触发的消费返佣扣回上级
        comm = db.find_commission_by_project(project_id)
        if comm:
            db.change_balance(comm["agent_id"], -comm["amount"],
                              f"返佣冲正：《{title}》项目退费")
            logger.info("返佣冲正: agent_uid=%d amount=%.2f project=%s",
                        comm["agent_id"], comm["amount"], project_id)

    # 删除作品：归属记录 + 项目目录 + 已导出的成品文件
    db.delete_project_owner(project_id)
    try:
        import shutil
        shutil.rmtree(store.dir, ignore_errors=True)
        from novel_gen.store.project import _safe_name
        for ext in ("txt", "epub", "docx", "png"):
            f = OUTPUT_DIR / f"{_safe_name(title)}.{ext}"
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass
    except Exception:
        logger.exception("删除项目文件失败: project=%s", project_id)

    balance = db.get_user(user["id"])["balance"]
    logger.info("项目退费删除: uid=%d project=%s title=%s price=%.2f new_balance=%.2f",
                user["id"], project_id, title, price, balance)
    return {"ok": True, "refunded": price, "balance": balance, "project_id": project_id}


@app.post("/api/projects/{project_id}/archive")
def archive_project(project_id: str, user: dict = Depends(_require_user)):
    """归档/取消归档项目。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    state = store.load_state()
    state.archived = not getattr(state, "archived", False)
    store.save_state(state)
    logger.info("项目归档: project=%s user=%s archived=%s", project_id, user["username"], state.archived)
    return {"ok": True, "archived": state.archived}


@app.post("/api/projects/{project_id}/clone")
def clone_project(project_id: str, user: dict = Depends(_require_user)):
    """克隆项目（复制配置+大纲，不复制正文）。"""
    import datetime, json, re
    _check_project_access(project_id, user)
    src_store = _find_project(project_id)
    cfg = src_store.load_config()
    # 用时间戳+安全文件名，避免中文/特殊字符导致路径问题
    safe_title = re.sub(r'[^\w\u4e00-\u9fa5-]', '_', cfg.title)[:30]
    new_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + safe_title
    new_dir = PROJECTS_DIR / new_id
    new_dir.mkdir(parents=True, exist_ok=True)
    try:
        src_cfg_file = src_store.dir / "config.json"
        if src_cfg_file.exists():
            cfg_data = json.loads(src_cfg_file.read_text(encoding="utf-8"))
            cfg_data["title"] = cfg.title + "（副本）"
            (new_dir / "config.json").write_text(json.dumps(cfg_data, ensure_ascii=False, indent=2), encoding="utf-8")
        outline = src_store.load_outline()
        if outline:
            (new_dir / "outline.json").write_text(json.dumps(outline.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        world = src_store.load_world()
        if world:
            (new_dir / "world.json").write_text(json.dumps(world.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        db.claim_project(new_id, user["id"], price=0.0)
        logger.info("克隆项目: src=%s user=%s new=%s", project_id, user["username"], new_id)
        return {"ok": True, "project_id": new_id}
    except Exception as e:
        logger.exception("克隆项目失败: src=%s err=%s", project_id, e)
        import shutil
        shutil.rmtree(new_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"克隆失败: {str(e)}")


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str, user: dict = Depends(_require_user)):
    """删除项目（仅站长或项目所有者，且未开始生成时可删）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    chapters = store.list_chapters()
    if chapters and not user.get("is_admin"):
        raise HTTPException(status_code=400, detail="项目已开始生成，无法删除")
    # 停止正在运行的线程
    handle = _RUNS.get(project_id)
    if handle:
        handle.stop_event.set()
        if handle.thread and handle.thread.is_alive():
            handle.thread.join(timeout=30)
    # 删除项目文件
    db.delete_project_owner(project_id)
    import shutil
    shutil.rmtree(store.dir, ignore_errors=True)
    return {"ok": True}


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
    store.save_outline(outline)
    return {"ok": True, "chapters": len(outline.chapters)}


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


@app.get("/api/projects/{project_id}/download")
def download(project_id: str, format: str = "txt", user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    cfg = store.load_config()
    name = _safe_name(cfg.effective_title())
    fmt = (format or "txt").lower()

    if fmt == "txt":
        media = "text/plain; charset=utf-8"
        out = OUTPUT_DIR / f"{project_id}_{name}.txt"
        if not out.exists():
            try:
                pipeline = _open_pipeline(store)
                src = pipeline.export()
                shutil.copy2(src, out)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=f"尚未生成可下载的 txt：{exc}")
    elif fmt == "epub":
        media = "application/epub+zip"
        out = OUTPUT_DIR / f"{project_id}_{name}.epub"
        if not out.exists():
            try:
                src = export_epub(store)
                shutil.copy2(src, out)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=f"导出 epub 失败：{exc}")
    elif fmt == "docx":
        media = ("application/vnd.openxmlformats-officedocument."
                 "wordprocessingml.document")
        out = OUTPUT_DIR / f"{project_id}_{name}.docx"
        if not out.exists():
            try:
                src = export_docx(store)
                shutil.copy2(src, out)
            except Exception as exc:
                raise HTTPException(status_code=404, detail=f"导出 docx 失败：{exc}")
    elif fmt == "md":
        out = OUTPUT_DIR / f"{project_id}_{name}.md"
        media = "text/markdown; charset=utf-8"
        if not out.exists():
            try:
                pipeline = _open_pipeline(store)
                chapters_dir = pipeline.store.dir / "chapters"
                lines = [f"# {cfg.effective_title()}", ""]
                if cfg.premise:
                    lines += [f"> {cfg.premise}", ""]
                for ch_file in sorted(chapters_dir.glob("ch*.txt")):
                    try:
                        text = ch_file.read_text(encoding="utf-8")
                        first_nl = text.find("\n")
                        title = text[:first_nl].strip() if first_nl > 0 else ch_file.stem
                        body = text[first_nl+1:].strip() if first_nl > 0 else ""
                        lines += [f"## {title}", "", body, ""]
                    except Exception:
                        continue
                out.write_text("\n".join(lines), encoding="utf-8")
            except Exception as exc:
                raise HTTPException(status_code=404, detail=f"导出 Markdown 失败：{exc}")
    else:
        raise HTTPException(status_code=400, detail=f"不支持的导出格式：{format}")

    if not out.exists():
        raise HTTPException(status_code=404, detail="尚未生成可下载的文件")
    logger.info("下载导出: uid=%d project=%s format=%s file=%s", user["id"], project_id, fmt, out.name)
    return FileResponse(str(out), filename=f"{name}.{fmt}", media_type=media)
