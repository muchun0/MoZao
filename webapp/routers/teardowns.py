# -*- coding: utf-8 -*-
"""爆款拆书库路由：题材聚合 / 列表 / 详情 / 删除（管理员）。

数据来自 book_teardown 表：research 沉淀（每次项目调研自动入库）+ seed 种子
（内置知识库导入）。浏览面向全部登录用户，删除仅站长。
"""
from ..core import *  # noqa: F401,F403  (app / _require_user / _require_admin / HTTPException / json)
from .. import db  # noqa: F401


@app.get("/api/teardowns/genres")
def teardown_genres(user: dict = Depends(_require_user)):
    """题材聚合（列表页筛选 chips）：[{genre, count}]，按 count 降序。"""
    return {"genres": db.list_teardown_genres()}


@app.get("/api/teardowns")
def teardown_list(genre: str = "", q: str = "", page: int = 1, page_size: int = 20,
                  user: dict = Depends(_require_user)):
    """拆书库列表（不含 content 全文）。支持题材筛选 + 标题/摘要关键词搜索。"""
    page = max(1, int(page))
    page_size = min(50, max(1, int(page_size)))
    return db.list_teardowns(genre=genre.strip(), q=q.strip(), page=page, page_size=page_size)


@app.get("/api/teardowns/{td_id}")
def teardown_detail(td_id: int, user: dict = Depends(_require_user)):
    """拆书条目详情：content 解析为 content_obj 结构（JSON 解析失败按 raw 文本返回）。"""
    td = db.get_teardown(td_id, inc_view=True)
    if not td:
        raise HTTPException(status_code=404, detail="拆书条目不存在")
    content = td.get("content") or ""
    try:
        parsed = json.loads(content) if content else {}
    except Exception:
        parsed = {"raw": content}
    td["content_obj"] = parsed
    td.pop("content", None)
    return td


@app.delete("/api/teardowns/{td_id}")
def teardown_delete(td_id: int, user: dict = Depends(_require_admin)):
    """删除拆书条目（管理员专用，用于清理劣质/过期沉淀）。"""
    if not db.delete_teardown(td_id):
        raise HTTPException(status_code=404, detail="拆书条目不存在")
    logger.info("管理员删除拆书条目: admin=%d td_id=%d", user["id"], td_id)
    return {"ok": True}
