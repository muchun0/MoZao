# -*- coding: utf-8 -*-
"""章节上游过期标记路由（档 1）：只读，判定逻辑在 novel_gen.pipeline.chapter_staleness。

约束：零 LLM、零写盘；统一用户身份校验 + 项目权限校验；
上游版本/索引读取异常在聚合层降级为 unknown + data_gaps，不返回 5xx。

注意注册顺序：本路由必须在 projects_chapters 之前注册，否则
`/chapters/{no}` 会先吃掉 `/chapters/staleness`（与 quality-overview 同理）。
"""
from ..core import *  # noqa: F401,F403  共享 app / 鉴权 / 项目定位
from novel_gen.pipeline import chapter_staleness as staleness


@app.get("/api/projects/{project_id}/chapters/staleness")
def chapters_staleness(project_id: str, user: dict = Depends(_require_user)):
    """逐章返回 fresh / stale / unknown 判定与依据，并给出过期章号汇总（只读）。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    return {"project_id": project_id, **staleness.build_chapter_staleness(store)}
