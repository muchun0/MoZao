# -*- coding: utf-8 -*-
"""projects_generation路由（第四批拆分，从 server.py 迁移，逻辑未改动）。"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层
from .. import generate_service  # noqa: F401  Agent 统一触发入口（阶段1B；不依赖 core，无循环 import）
from .. import agent_prompts  # noqa: F401  Agent 人设版本化资源（阶段1B）
from pydantic import BaseModel, Field


@app.post("/api/projects/{project_id}/agent-runs", status_code=202)
def start_agent_run(project_id: str, req: generate_service.AgentRunReq,
                    user: dict = Depends(_require_user)):
    """Agent 统一触发入口（TECH §4.1，阶段 1B）：守卫 → 占槽 → task.json → 后台线程。

    全局紧急回退开关 unified_generate=False 时整体 503（评审 I-12）。
    """
    _check_project_access(project_id, user)
    generate_service.assert_unified_generate_enabled()
    store = _find_project(project_id)
    return generate_service.start_agent_run(store, req, user)


@app.get("/api/projects/{project_id}/agent-runs/{task_id}")
def get_agent_run(project_id: str, task_id: str, user: dict = Depends(_require_user)):
    """Agent run 状态查询：running / resumed / done|stopped|failed / 404（I-6 四态状态机）。"""
    _check_project_access(project_id, user)
    generate_service.assert_unified_generate_enabled()
    store = _find_project(project_id)
    return generate_service.get_agent_run(store, task_id)


@app.post("/api/projects/{project_id}/agent-runs/{task_id}/stop")
def stop_agent_run(project_id: str, task_id: str, user: dict = Depends(_require_user)):
    """停止 agent run（阶段 1C）：与 /stop 共用 stop_event 机制；校验 task_id 匹配当前任务。"""
    _check_project_access(project_id, user)
    handle = _RUNS.get(project_id)
    if not handle or not handle.thread or not handle.thread.is_alive():
        return {"stopped": False, "task_id": task_id, "message": "任务未在运行"}
    if handle.task_id and handle.task_id != task_id:
        raise HTTPException(status_code=409, detail="task_id 与当前运行任务不匹配")
    handle.stop_event.set()
    return {"stopped": True, "task_id": task_id, "message": "已请求停止，当前章节完成后生效"}


class _AgentPromptReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


@app.get("/api/projects/{project_id}/agent-prompts")
def list_agent_prompts(project_id: str, user: dict = Depends(_require_user)):
    """全部可编辑 agent 的当前人设 + 版本号（无覆盖时懒快照默认人设为 v1，§3.4）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    prompts: dict = {}
    for agent in agent_prompts.EDITABLE_AGENTS:
        text, version, digest = agent_prompts.get_prompt_meta(store, agent)
        prompts[agent] = {"text": text, "version": version, "sha256": digest}
    return {"prompts": prompts}


@app.put("/api/projects/{project_id}/agent-prompts/{agent}")
def save_agent_prompt(project_id: str, agent: str, req: _AgentPromptReq,
                      user: dict = Depends(_require_user)):
    """保存用户人设为新不可变版本（author="user"）；非法 agent / 空文本 → 400。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    try:
        version, digest = agent_prompts.save_user_prompt(store, agent, req.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("用户人设保存: project=%s agent=%s version=%s uid=%d",
                project_id, agent, version, user["id"])
    return {"agent": agent, "version": version, "sha256": digest}


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
        vol_words = count_words("volume", {"name": vol.name, "intro": vol.intro,
                                           "chapters": [{"title": c.title, "goal": c.goal} for c in targets]})
        if store.versioned_enabled():
            # 版本化口径（阶段1A）：整纲提交为新版本；结算仍按本卷字数，幂等键=真实版本号
            v = store.save_outline(outline, commit=True, source="ai")
            if v is not None:
                settle_ledger.settle_version(store, "outline", "all", v, vol_words,
                                             mode=_settle_billing_mode(store),
                                             hq=bool(getattr(cfg, "high_quality_mode", False)),
                                             project_id=project_id)
        else:
            store.save_outline(outline)
            # v2 计费：分卷（卷简介 + 卷内章节规划）是一个独立产物，产出即结算一次
            _settle_product(store, "volume", str(vol_no),
                            {"name": vol.name, "intro": vol.intro,
                             "chapters": [{"title": c.title, "goal": c.goal} for c in targets]},
                            mode=_settle_billing_mode(store),
                            hq=bool(getattr(cfg, "high_quality_mode", False)),
                            project_id=project_id)
        logger.info("分卷重生成: project=%s vol=%d", project_id, vol_no)
        return {"ok": True, "intro": vol.intro, "chapters": [{"no": c.no, "title": c.title, "goal": c.goal} for c in targets]}
    except Exception as e:
        logger.exception("分卷重生成失败: project=%s vol=%d", project_id, vol_no)
        raise HTTPException(status_code=500, detail=f"重生成失败：{e}")


@app.get("/api/projects/{project_id}/outline/candidates")
def outline_candidates(project_id: str, user: dict = Depends(_require_user)):
    """P1-3-B 多版本候选（Sudowrite 式）：读编辑质检生成的修订候选列表，供用户挑选。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return {"candidates": store.load_outline_candidates()}


@app.post("/api/projects/{project_id}/outline/candidates/{index}/apply")
def apply_outline_candidate(project_id: str, index: int,
                            user: dict = Depends(_require_user)):
    """P1-3-B 用户挑选某候选 → 设为当前大纲。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    outline = store.apply_outline_candidate(int(index))
    if outline is None:
        raise HTTPException(status_code=404, detail="候选不存在或无法解析")
    return {"applied": True, "title": outline.title,
            "chapters": len(outline.chapters)}


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
            old_world = None
            try:
                pipeline = _open_pipeline(store)
                # 关键：必须删 world.json 文件，不能写空壳 World——
                # load_world() 对存在的文件一律返回对象（哪怕 world_view=""），
                # run_world() 的"已存在则复用"会把空壳当成品直接放行，
                # 导致重生成空跑、世界观与人物档案被清空。与 outline 重生成同模式。
                try:
                    old_world = store.load_world()
                except Exception:
                    old_world = None
                try:
                    store.world_path().unlink(missing_ok=True)
                except Exception:
                    pass
                pipeline.run_world()
                # v2 计费：世界观按产物版本提交并结算（成功产出才计费；失败恢复旧版不结算）
                _commit_and_settle(store, "world", store.load_world(),
                                   mode=_settle_billing_mode(store),
                                   hq=bool(getattr(store.load_config(), "high_quality_mode", False)),
                                   project_id=project_id)
            except Exception as e:
                handle.error = str(e)
                logger.exception("重生成世界观失败: project=%s", project_id)
                # 生成失败：恢复旧世界观，避免设定数据丢失
                try:
                    if old_world is not None:
                        store.save_world(old_world)
                except Exception:
                    logger.exception("重生成世界观失败后恢复旧设定异常: project=%s", project_id)
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


@app.post("/api/projects/{project_id}/chapters/regenerate")
def regenerate_all_chapters(project_id: str, user: dict = Depends(_require_user)):
    """整本重写全部章节正文（重新生成）：保留大纲与世界观，已锁定章节自动跳过。

    语义：复用批量生成（_run_batch 1..total），逐章覆盖重写；成本防护/余额耗尽时
    保留已生成产物，可从断点继续。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    outline = store.load_outline()
    if not outline or not outline.chapters:
        raise HTTPException(status_code=400, detail="尚无大纲章节，无法重写")
    handle = _acquire_slot(project_id)
    try:
        t = threading.Thread(target=_run_batch,
                             args=(store, handle, 1, len(outline.chapters)), daemon=True)
        handle.thread = t
        t.start()
    except Exception:
        _release_slot(project_id)
        raise
    logger.info("整本重写启动: project=%s chapters=%d user=%s",
                project_id, len(outline.chapters), user["username"])
    return {"started": True, "project_id": project_id, "start": 1, "end": len(outline.chapters)}


@app.post("/api/projects/{project_id}/stop")
def stop_generation(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    handle = _RUNS.get(project_id)
    if not handle:
        return {"stopped": False, "message": "项目未在运行"}
    handle.stop_event.set()
    return {"stopped": True, "project_id": project_id, "message": "已请求停止，当前章节完成后生效"}
