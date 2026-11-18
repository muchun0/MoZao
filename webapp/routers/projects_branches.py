# -*- coding: utf-8 -*-
"""R26 剧情走向候选路由：生成 / 采纳为便签 / 便签读取与删除。

只做 HTTP 装配（鉴权、参数、异常映射）；业务在 `webapp/branch_service.py`，
存储层在 `novel_gen/store/continue_drafts.py` 与 `inline_drafts`。旧
`/continue-branches` + `/apply-branch` 保持冻结（双轨期不计费）。
"""
from __future__ import annotations

from fastapi import Body

from ..core import *  # noqa: F401,F403  共享 app / 鉴权依赖 / 工具
from .. import branch_service
from ..branch_service import BranchError


def _fail(exc) -> None:
    """业务异常 → HTTP（detail 带 code 前缀，前端可原样展示）。"""
    raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.detail}")


@app.post("/api/projects/{project_id}/chapters/{no}/branches/generate")
def branches_generate(project_id: str, no: int, req: dict = Body(...),
                      user: dict = Depends(_require_user)):
    """生成 3~5 个剧情走向；计费开关开启时按候选合计字数结算一次。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    pipeline = _open_pipeline(store)
    try:
        return branch_service.generate(
            store, pipeline.llm, project_id=project_id, uid=int(user["id"]),
            payload={**(req or {}), "ch": no},
            request_id=getattr(pipeline, "trace_id", ""))
    except BranchError as exc:
        _fail(exc)


@app.post("/api/projects/{project_id}/chapters/{no}/branches/adopt")
def branches_adopt(project_id: str, no: int, req: dict = Body(...),
                   user: dict = Depends(_require_user)):
    """把某个走向存为写作便签：不改正文、不落版本、不再计费。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    try:
        return branch_service.adopt(store, project_id=project_id, ch_no=no, payload=req or {})
    except BranchError as exc:
        _fail(exc)


@app.get("/api/projects/{project_id}/chapters/{no}/branches/notes")
def branches_notes(project_id: str, no: int, user: dict = Depends(_require_user)):
    """该章写作便签列表（v2 多便签；旧单槽结构读时迁移）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return {"items": branch_service.list_notes(store, no)}


@app.delete("/api/projects/{project_id}/chapters/{no}/branches/notes/{draft_id}")
def branches_note_delete(project_id: str, no: int, draft_id: str,
                         user: dict = Depends(_require_user)):
    """删除单条便签：仅删便签，不退费、不改正文。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    try:
        return branch_service.remove_note(store, no, draft_id)
    except BranchError as exc:
        _fail(exc)
