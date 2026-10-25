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


@app.post("/api/projects/{project_id}/stop")
def stop_generation(project_id: str, user: dict = Depends(_require_user)):
    _check_project_access(project_id, user)
    handle = _RUNS.get(project_id)
    if not handle:
        return {"stopped": False, "message": "项目未在运行"}
    handle.stop_event.set()
    return {"stopped": True, "project_id": project_id, "message": "已请求停止，当前章节完成后生效"}
