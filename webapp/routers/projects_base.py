# -*- coding: utf-8 -*-
"""projects_base路由（第四批拆分，从 server.py 迁移，逻辑未改动）。"""
import shutil  # noqa: F401  下载导出/删除/克隆均需复制与清理
import re  # noqa: F401  用户大纲导入：标题/章节解析

from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层


def _import_user_outline(store, text: str) -> bool:
    """把用户上传的大纲【原样导入】项目（以用户传入为准，不做 AI 重写）。

    解析大纲文本为结构化 Outline（书名/总纲/章节），写入 outline.json，
    让项目详情「大纲」tab 立即可见可编辑；AI 重写仅发生在用户显式点击
    「重生成大纲」时（该按钮删除旧大纲后才走 LLM 扩写）。
    识别不到任何章节标题时返回 False（不落盘，保留「生成大纲与世界观」入口）。
    """
    text = (text or "").strip()
    if not text:
        return False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # 书名：取首个非空行（去掉 markdown # 前缀与书名号）
    title = re.sub(r"^#+\s*", "", lines[0]).strip("《》 \t") if lines else ""

    intro = ""
    plans = []
    for ch in split_chapters(text):
        body = (ch.get("content") or "").strip()
        if not ch.get("title"):
            # 无标题段落（通常是总纲/前导说明）归入总纲，不占章节
            intro = (intro + "\n" + body).strip() if intro else body
            continue
        plans.append(ChapterPlan(
            no=len(plans) + 1,
            title=ch["title"],
            goal=body,  # 章节正文完整保留（以用户传入为准），用户可继续编辑
        ))
    if not plans:
        # 未识别到章节结构：不落盘，避免空大纲阻塞「生成大纲与世界观」
        logger.info("用户大纲未识别到章节结构，跳过导入: project=%s", store.dir.name)
        return False
    outline = Outline(
        title=title or store.load_config().effective_title(),
        intro=intro,
        chapters=plans,
    )
    store.save_outline(outline)
    logger.info("导入用户大纲: project=%s title=%s chapters=%d",
                store.dir.name, outline.title, len(outline.chapters))
    return True


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

    # 注入写作风格 skill（单技能，向后兼容）
    if req.skill_id:
        _sk = db.get_skill(req.skill_id)
        if _sk and _sk["is_active"]:
            cfg.skill_prompt_append = _sk["prompt_append"] or ""

    # 注入写作风格角色（风格师）：人设 + 挂载技能组装后快照进 config
    if req.style_role_id:
        _sr = db.get_style_role_assembled(req.style_role_id)
        if _sr:
            cfg.style_role_id = _sr["id"]
            cfg.style_role_prompt = _sr["assembled"]

    # 配置校验（非硬约束问题仅警告）
    issues = validate_config(cfg)
    real_issues = [i for i in issues if "硬约束" not in i]

    # 扩写模式校验：纯扩写需大纲/简要内容；续写/扩写已有章节需已有正文
    user_content = (req.user_content or "").strip()
    # 粘贴上限（需求确认）：单次粘贴不超过 10 万字，超出请拆分或改用文件上传
    if len(req.expand_input) > MAX_PASTE_CHARS or len(user_content) > MAX_PASTE_CHARS:
        raise HTTPException(status_code=400,
                            detail="粘贴内容超过 10 万字上限，请拆分后分次粘贴，或改用文件上传（5MB 以内）")
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

    # AI 扩写（提供大纲）模式：把用户上传的大纲【原样导入】项目（以用户传入为准，
    # 不做 AI 重写），让「大纲」tab 立即可见可编辑；AI 重写只在用户点击「重生成大纲」时发生。
    # 世界观/人物不自动生成：用户需在项目页手动点「重生成世界观」触发。
    outline_imported = False
    if req.mode == "expand" and not req.content_type and req.expand_input.strip():
        try:
            outline_imported = _import_user_outline(store, req.expand_input)
        except Exception:
            logger.exception("导入用户大纲失败: project=%s", store.dir.name)

    logger.info("创建项目(未扣费): uid=%d project=%s title=%s mode=%s content_type=%s "
                "预估=%.2f(原价%.2f) total_words=%d user_words=%d ai_words=%d",
                user["id"], store.dir.name, cfg.effective_title(), cfg.mode, cfg.content_type,
                price, base_price, cfg.total_words, user_words, ai_words)
    return {
        "id": store.dir.name,
        "title": cfg.effective_title(),
        "outline_imported": outline_imported,  # 用户大纲是否已识别导入（False=未识别，前端提示补格式重传）
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

@app.post("/api/projects/{project_id}/outline/import")
def import_user_outline(project_id: str, req: OutlineImportReq, user: dict = Depends(_require_user)):
    """重新导入/覆盖用户大纲：以用户传入为准（原样入库，不 AI 重写）。

    - 覆盖前自动备份当前大纲为快照（outline_snapshots/导入前_MMdd_HHmm.json），项目页可一键还原；
    - 识别不到章节结构时返回 400 并提示补格式；用户选择不补格式时，
      可点项目页「生成大纲与世界观」——按钮点击即用户确认，AI 才会生成。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中，请先停止再导入大纲")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="导入内容为空")
    if len(text) > MAX_PASTE_CHARS:
        raise HTTPException(status_code=400, detail="粘贴内容超过 10 万字上限，请拆分或改用文件上传")
    # 覆盖前自动备份上一版（一键还原）
    _backup_outline(store, "导入前")
    imported = _import_user_outline(store, text)
    if not imported:
        raise HTTPException(
            status_code=400,
            detail="未能识别到章节结构：请补充格式后重新上传（如为每章添加「第X章」标题）；"
                   "若不补格式，也可在项目页点『生成大纲与世界观』由 AI 生成（需你确认）。",
        )
    return {"ok": True, "imported": True, "chapters": len(store.load_outline().chapters)}
