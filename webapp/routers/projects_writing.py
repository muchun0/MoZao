# -*- coding: utf-8 -*-
"""写作侧栏只读路由（REQ-R29）：章计划 + 出场人物 + 本章伏笔 + 五态计数。

只读装配：鉴权/项目权限 → 聚合 → 返回；业务在 `webapp/writing_context.py`，
不写盘、不调用模型、不计费。
"""
from __future__ import annotations

from ..core import *  # noqa: F401,F403  共享 app / 鉴权依赖 / 工具
from .. import writing_context
from ..writing_context import WritingContextError


@app.get("/api/projects/{project_id}/writing-context")
def get_writing_context(project_id: str, ch: int = 0,
                        user: dict = Depends(_require_user)):
    """写作侧栏单次只读聚合：当前章的章计划、出场人物、本章相关伏笔与五态计数。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    try:
        return writing_context.build_writing_context(store, ch)
    except WritingContextError as exc:
        raise HTTPException(status_code=400, detail=f"BAD_CHAPTER: {exc}")
