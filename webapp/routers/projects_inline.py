# -*- coding: utf-8 -*-
"""R25 内联写作路由：生成 / 采纳 / 草稿 / 预估 + 只读全文检索。

本模块只做 HTTP 装配（鉴权、参数、异常映射）；业务逻辑在 `webapp/inline_service.py`
与 `webapp/search_service.py`，SQL 只在 `webapp/db/`。
"""
from __future__ import annotations

from fastapi import Body

from ..core import *  # noqa: F401,F403  共享 app / 鉴权依赖 / 工具
from .. import inline_service, search_service
from ..inline_service import InlineError
from ..search_service import SearchError


def _fail(exc) -> None:
    """业务异常 → HTTP（detail 带 code 前缀，前端可原样展示）。"""
    raise HTTPException(status_code=exc.status_code, detail=f"{exc.code}: {exc.detail}")


@app.post("/api/projects/{project_id}/chapters/{no}/inline/generate")
def inline_generate(project_id: str, no: int, req: dict = Body(...),
                    user: dict = Depends(_require_user)):
    """生成内联候选（生成即计费；未采纳也计费，前端须事前提示）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    if store.load_chapter(no) is None:
        raise HTTPException(status_code=404, detail="CHAPTER_NOT_FOUND: 章节未生成")
    pipeline = _open_pipeline(store)
    try:
        return inline_service.generate(
            store, pipeline.llm, project_id=project_id, uid=int(user["id"]),
            payload={**(req or {}), "ch": no}, request_id=getattr(pipeline, "trace_id", ""))
    except InlineError as exc:
        _fail(exc)


@app.post("/api/projects/{project_id}/chapters/{no}/inline/adopt")
def inline_adopt(project_id: str, no: int, req: dict = Body(...),
                 user: dict = Depends(_require_user)):
    """采纳候选：按记录的 anchor 写入新版本，不再二次计费。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    try:
        return inline_service.adopt(store, project_id=project_id, uid=int(user["id"]),
                                    ch_no=no, payload=req or {})
    except InlineError as exc:
        _fail(exc)


@app.post("/api/projects/{project_id}/chapters/{no}/inline/apply-suggestion")
def inline_apply_suggestion(project_id: str, no: int, req: dict = Body(...),
                            user: dict = Depends(_require_user)):
    """R28 应用小墨建议：按 message_id 回读校验 → 结算 → 基座写通道（source=inline）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    from ..assistant.inline_apply import ApplyError, apply_suggestion
    try:
        return apply_suggestion(store, project_id=project_id, uid=int(user["id"]),
                                ch_no=no, payload=req or {},
                                is_admin=bool(user.get("is_admin")))
    except ApplyError as exc:
        _fail(exc)


@app.get("/api/projects/{project_id}/chapters/{no}/inline/drafts")
def inline_drafts(project_id: str, no: int, user: dict = Depends(_require_user)):
    """该章 48h 内未采纳候选（重开面板不重复计费）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return {"items": inline_service.list_drafts(store, no)}


@app.get("/api/projects/{project_id}/chapters/{no}/inline/estimate")
def inline_estimate(project_id: str, no: int, chars: int = 400, candidates: int = 2,
                    user: dict = Depends(_require_user)):
    """触发前只读预估（不扣费、不调用模型）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return inline_service.estimate(store=store, project_id=project_id,
                                   chars=chars, candidates=candidates)


@app.get("/api/projects/{project_id}/search")
def search_in_project(project_id: str, q: str = "", limit: int = 20, offset: int = 0,
                      scope: str = "all", user: dict = Depends(_require_user)):
    """只读全文检索（正文 / 设定），供 R29 搜索与 R28 定位共用。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    if not search_service.search_enabled():
        raise HTTPException(status_code=403, detail="SEARCH_DISABLED: 写作侧栏搜索未开启")
    try:
        return search_service.search_project(store, q=q, limit=limit, offset=offset, scope=scope)
    except SearchError as exc:
        _fail(exc)
