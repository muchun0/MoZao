# -*- coding: utf-8 -*-
"""章节质量总览路由（R12）：只读接口，聚合逻辑在 novel_gen.pipeline.quality_overview。

约束：零 LLM、零写盘；统一用户身份校验 + 项目权限校验；
索引读取异常在聚合层降级为空结果 + data_gaps，不返回 5xx。
"""
from ..core import *  # noqa: F401,F403  共享 app / 鉴权 / 项目定位
from novel_gen.pipeline import quality_overview as overview


@app.get("/api/projects/{project_id}/chapters/quality-overview")
def chapters_quality_overview(project_id: str, user: dict = Depends(_require_user)):
    """各已写章节「当前选中版本」的质检分、问题严重度与最差排序（只读）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return {"project_id": project_id, **overview.build_quality_overview(store)}
