# -*- coding: utf-8 -*-
"""projects_chapters路由（第四批拆分，从 server.py 迁移，逻辑未改动）。"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from fastapi import Body  # P2-3 申诉接口 body（core 未导出）
from .. import db  # noqa: F401  数据层
from novel_gen.pipeline.style_anchor import (
    PERSONAL_ANCHOR_HEADER, extract_personal_anchor, wrap_style_anchor)
from ..user_memory import UserMemoryService  # 用户级记忆库（跨书创作偏好继承）
from ..version_recommend import attach_chapter_recommendations
from novel_gen.pipeline.templates import _style_reference
import time


@app.get("/api/projects/{project_id}/chapters/{no}/versions")
def list_chapter_versions(project_id: str, no: int, user: dict = Depends(_require_user)):
    """章节版本列表（REQ-章节版本化）：返回全部保留版本 + 当前选中标记 + 推荐徽章。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    if store.load_chapter(no) is None:
        raise HTTPException(status_code=404, detail=f"章节 {no} 未生成")
    versions = attach_chapter_recommendations(store, no, store.list_versions(no))
    return {"no": no, "versions": versions}


@app.post("/api/projects/{project_id}/chapters/{no}/versions/{v}/select")
def select_chapter_version(project_id: str, no: int, v: int,
                           user: dict = Depends(_require_user)):
    """回退/选中章节版本（REQ-章节版本化）：把 v{v} 复制回当前选中（HEAD）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    if store.load_chapter(no) is None:
        raise HTTPException(status_code=404, detail=f"章节 {no} 未生成")
    _assert_not_locked(store, "chapter", no, "本章已被用户锁定，如需切换版本请先解锁")
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
    ch = store.select_version(no, v)
    if ch is None:
        raise HTTPException(status_code=400, detail=f"版本 v{v} 不存在（可能已被淘汰）")
    # P2（REQ §5 验收10）：回退后账本为旧版本投影，仅提示，不重放
    logger.info("章节版本回退: uid=%d project=%s no=%d v=%d", user["id"], project_id, no, v)
    return {"no": no, "selected_version": v, "words": len(ch.content)}


@app.delete("/api/projects/{project_id}/chapters/{no}/versions/{v}")
def delete_chapter_version(project_id: str, no: int, v: int,
                           user: dict = Depends(_require_user)):
    """删除章节版本（版本质量扩展）：删除指定历史版本存档（当前选中版本不可删）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    if store.load_chapter(no) is None:
        raise HTTPException(status_code=404, detail=f"章节 {no} 未生成")
    _assert_not_locked(store, "chapter", no, "本章已被用户锁定，如需删除版本请先解锁")
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
    ok = store.delete_version(no, v)
    if not ok:
        raise HTTPException(status_code=400, detail=f"版本 v{v} 不存在或为当前使用中版本，无法删除")
    logger.info("章节版本删除: uid=%d project=%s no=%d v=%d", user["id"], project_id, no, v)
    versions = attach_chapter_recommendations(store, no, store.list_versions(no))
    return {"no": no, "deleted_version": v, "versions": versions}


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
    # 用户级记忆库：人工编辑信号 → 个人文风锚跨书登记（零 LLM，失败静默降级）
    try:
        anchors = extract_personal_anchor([ch])
        if anchors:
            UserMemoryService(user["id"]).record_edits(project_id, no, anchors)
    except Exception:
        logger.exception("用户级记忆编辑信号失败（降级跳过）: project=%s no=%d",
                         project_id, no)
    # REQ-写作正向引导 5.6：用户编辑保存 = 用户认可好文风 → 入 good 账本
    # （零 LLM，失败静默降级；与 user_edited 标记同处触发）
    try:
        from novel_gen.pipeline.book_memory import BookMemory
        BookMemory(store, store.load_config()).record_good_manual(no, ch.content)
    except Exception:
        logger.exception("good 账本人工修订入账失败（降级跳过）: project=%s no=%d",
                         project_id, no)
    # REQ-章节版本化：用户编辑保存 = 追加一个 source=user 版本（不覆盖旧版）
    store.save_chapter(ch, source="user")
    if ch.summary:
        store.append_summary(no, ch.summary)
    logger.info("章节编辑: uid=%d project=%s no=%d title=%s words=%d",
                user["id"], project_id, no, ch.title, len(ch.content))
    return {"no": ch.no, "title": ch.title, "summary": ch.summary, "words": len(ch.content)}


# =====================================================================
# 三期产品化能力（2026-10-14）：场景体检 / 三分支续写 / 思维链可视化 / Beat 门禁读取
# =====================================================================

@app.get("/api/projects/{project_id}/chapters/{no}/scene-inspect")
def scene_inspect_chapter(project_id: str, no: int, user: dict = Depends(_require_user)):
    """F2 场景体检：对已生成章节做字数/事件推进/重复度/承接/钩子确定性体检。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    cfg = store.load_config()
    if not getattr(cfg, "scene_inspector", True):
        raise HTTPException(status_code=400, detail="场景体检已关闭")
    from novel_gen.pipeline.scene_inspector import inspect_chapter, collect_suggestions
    report = inspect_chapter(store, no, target_words=int(getattr(cfg, "chapter_words", 0) or 0))
    # P1-5 读时体检 → 生成反馈闭环：把 bad/warn 维度的建议沉淀进 store，
    # 下一章生成时自动携带（失败静默，不阻塞体检展示）。
    try:
        store.save_inspector_suggestions(no, collect_suggestions(report))
    except Exception:
        import logging
        logging.getLogger(__name__).warning("场景体检建议落盘失败（降级，仅展示不闭环）", exc_info=True)
    # 附带 beat 覆盖门禁结果（F1 落盘产物）
    bg = store.load_beat_gate().get(str(no))
    report["beat_gate"] = bg
    return report


@app.post("/api/projects/{project_id}/chapters/{no}/appeal")
def appeal_quality(project_id: str, no: int,
                   reason: str = Body(..., description="申诉理由"),
                   issue_type: str = Body("", description="被申诉的质检问题类型"),
                   user: dict = Depends(_require_user)):
    """P2-3 申诉撤销：用户对某章质检/合规判罚发起申诉（误判兜底）。

    申诉沉淀进 store.appeals.json；合规词表命中=疑似可能误报，作者可据此申诉。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    aid = f"a{int(time.time() * 1000)}{no}"
    store.save_appeal({
        "id": aid, "chapter": no,
        "issue_type": (issue_type or "").strip(),
        "reason": (reason or "").strip(),
        "status": "pending", "created_at": int(time.time()),
    })
    return {"appeal_id": aid, "status": "pending"}


@app.post("/api/projects/{project_id}/appeals/{appeal_id}/overturn")
def overturn_appeal(project_id: str, appeal_id: str,
                    user: dict = Depends(_require_user)):
    """P2-3 申诉撤销：把一条申诉标记为撤销（overturned），计入误判率。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    ok = store.mark_appeal_overturned(appeal_id)
    if not ok:
        raise HTTPException(status_code=404, detail="申诉不存在")
    return {"overturned": True, "misjudge": store.misjudge_rate()}


@app.get("/api/projects/{project_id}/appeals")
def list_appeals(project_id: str, user: dict = Depends(_require_user)):
    """P2-3 申诉列表 + 误判率指标（只读）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return {"appeals": store.load_appeals(), "misjudge": store.misjudge_rate()}


@app.get("/api/projects/{project_id}/chapters/{no}/chain-of-thought")
def chapter_chain_of_thought(project_id: str, no: int, user: dict = Depends(_require_user)):
    """F4 思维链可视化（只读聚合）：计划（大纲拍）→产出（拆场景）→校验（beat门禁/质检）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    cfg = store.load_config()
    if not getattr(cfg, "chain_of_thought", True):
        raise HTTPException(status_code=400, detail="思维链可视化已关闭")
    # 计划段：大纲该章字段
    plan = {}
    outline = store.load_outline()
    if outline and 1 <= no <= len(outline.chapters):
        p = outline.chapters[no - 1]
        plan = {"goal": p.goal, "core_event": p.core_event,
                "character_moves": p.character_moves, "cliffhanger": p.cliffhanger,
                "tag": getattr(p, "tag", "")}
    # 产出段：拆场景
    production = store.load_scenes().get(str(no)) or {}
    # 校验段：beat 门禁 + review.json 该章最新一条
    review = {}
    bg = store.load_beat_gate().get(str(no))
    if bg:
        review["beat_gate"] = bg
    try:
        rp = store.dir / "review.json"
        if rp.exists():
            import json as _json
            for r in reversed(_json.loads(rp.read_text(encoding="utf-8"))):
                if int(r.get("chapter", 0)) == no:
                    qc = r.get("quality_check") or {}
                    review["quality"] = {"score": qc.get("score"), "passed": qc.get("passed"),
                                         "issue_count": len(r.get("issues") or [])}
                    break
    except Exception:
        logger.warning("[ChainOfThought] 读取 review.json 失败（降级）: project=%s ch=%d",
                       project_id, no)
    return {"chapter": no, "plan": plan, "production": production, "review": review}


_BRANCH_DIRS = [
    ("escalate", "冲突升级", "让现有冲突更激烈、矛盾直接激化、主角被逼到墙角"),
    ("foreshadow", "埋伏笔", "引出新线索/新人物/新谜团，为后文埋钩子，暂不爆发"),
    ("twist", "剧情转折", "出现意外反转，打破读者预期（身份揭露/立场突变/局势倒转）"),
]


@app.post("/api/projects/{project_id}/continue-branches")
def continue_branches(project_id: str, req: dict, user: dict = Depends(_require_user)):
    """F3 三分支续写：基于基准文本一次生成 3 个方向明显不同的续写候选。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    cfg = store.load_config()
    if not getattr(cfg, "branch_continue", True):
        raise HTTPException(status_code=400, detail="三分支续写已关闭")
    chapter_no = int(req.get("chapter_no", 0) or 0)
    base = (req.get("text") or "").strip()
    if not base and chapter_no >= 1:
        ch = store.load_chapter(chapter_no)
        base = (ch.content or "")[-600:] if ch else ""
    if not base:
        raise HTTPException(status_code=400, detail="缺少续写基准文本")
    dir_desc = "；".join(f"{i+1}) {label}：{desc}" for i, (_k, label, desc) in enumerate(_BRANCH_DIRS))
    sys = (
        "你是一位网络小说续写策划。基于给定的上文结尾，给出 3 个方向明显不同的续写分支。"
        "严格 JSON 数组返回，恰好 3 项，每项包含 direction、label、preview 三个字段，"
        "其中 direction 取值 escalate/foreshadow/twist，preview 为 150-300 字续写正文段落。"
        "三方向分别为：" + dir_desc + "。"
        "preview 必须是可直接接上上文的正文，不要解释、不要列大纲。"
    )
    try:
        pipeline = _open_pipeline(store)
        llm = pipeline.llm
        raw = llm.chat(sys, f"【上文结尾】\n{base}", temperature=0.8, max_tokens=1200)
        import json as _json, re as _re
        raw = (raw or "").strip()
        m = _re.search(r"\[.*\]", raw, _re.S)
        branches = []
        if m:
            try:
                branches = _json.loads(m.group(0))
            except Exception:
                branches = []
        branches = [b for b in (branches or []) if isinstance(b, dict) and b.get("preview")][:3]
    except Exception as e:
        logger.exception("[ContinueBranches] 三分支生成失败: project=%s", project_id)
        raise HTTPException(status_code=500, detail=f"生成失败：{e}")
    logger.info("[ContinueBranches] uid=%d project=%s ch=%d branches=%d",
                user["id"], project_id, chapter_no, len(branches))
    return {"branches": branches, "base_tail": base[-200:]}


@app.post("/api/projects/{project_id}/apply-branch")
def apply_branch(project_id: str, req: dict, user: dict = Depends(_require_user)):
    """F3 应用选中分支：默认预览模式——把选中的续写草稿记录到 continue_drafts.json，
    不自动改写章节正文（由用户在编辑器里自行粘贴，防污染既有正文链路）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    chapter_no = int(req.get("chapter_no", 0) or 0)
    preview = (req.get("preview") or "").strip()
    if not preview:
        raise HTTPException(status_code=400, detail="缺少分支续写内容")
    draft = {"direction": req.get("direction", ""), "label": req.get("label", ""),
             "preview": preview, "applied_at": time.time()}
    try:
        store.save_continue_draft(chapter_no, draft)
    except Exception:
        logger.exception("[ApplyBranch] 续写草稿落盘失败（仍返回预览）: project=%s ch=%d",
                         project_id, chapter_no)
    return {"saved": True, "chapter_no": chapter_no, "preview": preview,
            "msg": "续写草稿已记录（预览模式，未自动写入章节正文，请复制到编辑器使用）"}

