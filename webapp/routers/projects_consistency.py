# -*- coding: utf-8 -*-
"""全书体检路由（R2）：只读接口，全部收敛到 novel_gen.pipeline.consistency_report。

约束：不触发重写、不写库、不落盘；所有接口统一用户身份校验 + 项目权限校验。
"""
from ..core import *  # noqa: F401,F403  共享 app / 鉴权 / 项目定位
from fastapi import Query
from novel_gen.pipeline import consistency_report as report


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def _open_store(project_id: str, user: dict):
    _check_project_access(project_id, user)
    return _find_project(project_id)


@app.get("/api/projects/{project_id}/consistency/overview")
def consistency_overview(project_id: str, user: dict = Depends(_require_user)):
    """全书体检首屏：账本覆盖计数 / 伏笔状态计数 / 一致性冲突计数 + 各分类前 N 条。"""
    store = _open_store(project_id, user)
    return report.build_overview(store, store.load_config())


@app.get("/api/projects/{project_id}/consistency/coverage")
def consistency_coverage(project_id: str, up_to: int = 0,
                         user: dict = Depends(_require_user)):
    """账本覆盖明细（分页无需：视图数量固定）；up_to=0 表示以当前已写最大章为基准。"""
    store = _open_store(project_id, user)
    return report.build_coverage(store, up_to_chapter=(up_to or None))


@app.get("/api/projects/{project_id}/consistency/foreshadow")
def consistency_foreshadow(project_id: str, state: str = "",
                           limit: int = 50, offset: int = 0,
                           user: dict = Depends(_require_user)):
    """伏笔台账（可按状态筛选：overdue/stalled/due_soon/planted/revealed）。"""
    store = _open_store(project_id, user)
    items = report.build_foreshadow(store, store.load_config())
    if state:
        items = [i for i in items if i["state"] == state]
    total = len(items)
    offset = _clamp(offset, 0, total)
    return {"items": items[offset: offset + _clamp(limit, 1, 200)],
            "total": total, "offset": offset}


@app.get("/api/projects/{project_id}/consistency/conflicts")
def consistency_conflicts(project_id: str, severity: str = "",
                          limit: int = 50, offset: int = 0,
                          user: dict = Depends(_require_user)):
    """一致性冲突清单（全书校验 + 章节质检），按严重度排序。

    额外下发 sources（计数口径）：全书校验 / 单章质检各多少条、合并去重后多少条，
    让前端能解释「工具 21 条、体检 36 条」这类差异，而不是再给第四套数字。
    """
    store = _open_store(project_id, user)
    items, sources = report.build_conflicts_with_counts(store)
    total = len(items)
    if severity:
        items = [i for i in items if i["severity"] == severity]
    page_total = len(items)
    offset = _clamp(offset, 0, page_total)
    return {"items": items[offset: offset + _clamp(limit, 1, 200)],
            "total": page_total, "merged_total": total,
            "sources": sources, "offset": offset}


@app.get("/api/projects/{project_id}/consistency/entities")
def consistency_entities(project_id: str, q: str = "", kind: str = "",
                         limit: int = 50, offset: int = 0,
                         user: dict = Depends(_require_user)):
    """人物/设定提及索引：每个实体的出现章节与次数（流式扫描，不载入全书）。"""
    store = _open_store(project_id, user)
    return report.build_entity_index(store, query=q, kind=kind,
                                     limit=_clamp(limit, 1, 200), offset=_clamp(offset, 0, 10_000))


@app.get("/api/projects/{project_id}/consistency/mentions")
def consistency_mentions(project_id: str, name: str,
                         alias: list[str] = Query(default=[]),
                         limit_chapters: int = 50,
                         user: dict = Depends(_require_user)):
    """单个实体的章节内定位：每章命中次数 + 原文片段 + 字符偏移。"""
    store = _open_store(project_id, user)
    if not (name or "").strip():
        raise HTTPException(status_code=400, detail="name 不能为空")
    return report.search_mentions(store, name, alias,
                                  limit_chapters=_clamp(limit_chapters, 1, 200))
