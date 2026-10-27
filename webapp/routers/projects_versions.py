# -*- coding: utf-8 -*-
"""产物版本路由（TECH-Agent用户化与版本化 §10.1/§11.3，阶段 1C）。

outline/world 产物级版本库（阶段 1A 门面）的 HTTP 暴露：
- 版本列表（每版附计费字数 + 结算金额，金额从 settle.json 账本反查）；
- 受控回滚 select（置 user_managed，不改写已生成正文）；
- 回滚影响范围预估（派生视图过期清单 + 需重生成起始章，供前端确认弹窗）。

chapter 版本仍走 projects_chapters.py 既有路由，不在本文件重复暴露。
"""
from ..core import *  # noqa: F401,F403  共享状态/工具/模型/鉴权依赖
from .. import db  # noqa: F401  数据层
from .. import settle_ledger
from ..version_recommend import attach_product_recommendations
from novel_gen.store.versioned import VersionedProduct
from novel_gen.pipeline.commit import check_projection
from novel_gen.pipeline.version_diff import diff_chapter_text, diff_structured

# list/select/rollback 只服务单实例产物（key="all"）；diff 接口额外放行 chapter
_PRODUCT_KINDS = ("outline", "world")
_DIFF_KINDS = ("outline", "world", "chapter")
_PRODUCT_KEY = "all"


def _product(store, kind: str, allowed: tuple = _PRODUCT_KINDS) -> VersionedProduct:
    """kind 白名单校验 + 门面构造（越界 kind 一律 400，不触碰文件系统）。"""
    if kind not in allowed:
        raise HTTPException(status_code=400,
                            detail=f"不支持的产物类型：{kind}（支持 {', '.join(allowed)}）")
    return VersionedProduct(store, kind)


@app.get("/api/projects/{project_id}/products/{kind}/versions")
def list_product_versions(project_id: str, kind: str, user: dict = Depends(_require_user)):
    """产物版本列表：versioned.list（含 words）+ 每版结算金额（账本反查）+ 推荐徽章。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    vp = _product(store, kind)
    versions = vp.list(_PRODUCT_KEY)
    charges = settle_ledger.version_charge_map(store, kind, _PRODUCT_KEY)
    for item in versions:
        charge = charges.get(int(item.get("v") or 0))
        if charge is not None:
            item["charged"] = charge["charged"]
            item["due"] = charge["due"]
    versions = attach_product_recommendations(store, versions)
    return {"kind": kind, "versions": versions}


@app.get("/api/projects/{project_id}/products/{kind}/versions/diff")
def diff_product_versions(project_id: str, kind: str, v1: int, v2: int,
                           chapter_no: int | None = None,
                           user: dict = Depends(_require_user)):
    """两版只读对比（REQ-版本对比与推荐）：零 LLM、不写盘、不扣费。

    - kind=chapter：必须传 chapter_no；返回段落级正文 diff；
    - kind=outline/world：返回字段级结构化 diff。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    vp = _product(store, kind, allowed=_DIFF_KINDS)
    key = str(chapter_no) if kind == "chapter" else _PRODUCT_KEY
    if kind == "chapter" and not chapter_no:
        raise HTTPException(status_code=400, detail="对比章节版本必须传 chapter_no")

    a = vp.load_version(key, v1)
    b = vp.load_version(key, v2)
    if a is None or b is None:
        raise HTTPException(status_code=404, detail="指定版本不存在")

    if kind == "chapter":
        result = diff_chapter_text(a.get("content") or "", b.get("content") or "")
        result.update({"kind": kind, "chapter_no": chapter_no, "v1": v1, "v2": v2})
    else:
        changes = diff_structured(a, b)
        # 按 group 聚合
        groups: dict[str, list] = {}
        for c in changes:
            groups.setdefault(c["group"], []).append(c)
        result = {
            "kind": kind, "v1": v1, "v2": v2,
            "groups": [{"group": g, "changes": cs} for g, cs in groups.items()],
            "summary": {"added": sum(1 for c in changes if c["type"] == "added"),
                        "removed": sum(1 for c in changes if c["type"] == "removed"),
                        "modified": sum(1 for c in changes if c["type"] == "modified")},
        }
    logger.info("版本 diff: project=%s kind=%s v%d-v%d uid=%d",
                project_id, kind, v1, v2, user["id"])
    return result


@app.post("/api/projects/{project_id}/products/{kind}/versions/{v}/select")
def select_product_version(project_id: str, kind: str, v: int,
                           user: dict = Depends(_require_user)):
    """受控回滚（§11.3）：select 置 user_managed；只切 HEAD，不改写/删除已生成正文。"""
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    vp = _product(store, kind)
    data = vp.select(_PRODUCT_KEY, v)
    if data is None:
        raise HTTPException(status_code=404, detail=f"版本 v{v} 不存在")
    logger.info("产物版本回滚: project=%s kind=%s v=%d uid=%d",
                project_id, kind, v, user["id"])
    return {"kind": kind, "selected": v, "versions": vp.list(_PRODUCT_KEY)}


@app.get("/api/projects/{project_id}/products/{kind}/rollback-impact")
def rollback_impact(project_id: str, kind: str, v: int,
                    user: dict = Depends(_require_user)):
    """回滚影响范围（§11.3，评审 S-6）：派生视图过期清单 + 受影响章节 + 需重生成起始章。

    受影响章判定：章节版本 history 中当前选中版本的 upstream 记录了生成时依赖的
    outline_v/world_v；若其值 > 回滚目标 v，说明该章基于「更新的上游版本」生成，
    回滚后与上游不一致，需重生成。无 upstream 记录的存量章无法精确判定，不计入
    （降级为只提示派生视图状态）。
    """
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    _product(store, kind)  # 仅做 kind 白名单校验
    dep_key = f"{kind}_v"
    affected: list[int] = []
    for no_str, rec in (store.load_chapter_versions() or {}).items():
        if not str(no_str).isdigit() or not isinstance(rec, dict):
            continue
        selected = rec.get("selected")
        for h in (rec.get("history") or []):
            if not isinstance(h, dict) or h.get("v") != selected:
                continue
            upstream = h.get("upstream") or {}
            dep_v = upstream.get(dep_key)
            if isinstance(dep_v, int) and dep_v > v:
                affected.append(int(no_str))
            break
    views = check_projection(store)
    stale_views = [x for x in views if x.get("status") == "lagging"]
    generated = [int(c.no) for c in store.list_chapters() if c.no is not None]
    return {
        "kind": kind,
        "target_version": v,
        "affected_chapters": sorted(affected),
        "regenerate_from": min(affected) if affected else None,
        "generated_count": len(generated),
        "stale_views": stale_views,
    }
