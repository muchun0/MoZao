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
from novel_gen.store.versioned import VersionedProduct
from novel_gen.pipeline.commit import check_projection

# 本路由只服务单实例产物（一个项目一份，key 恒为 "all"）
_PRODUCT_KINDS = ("outline", "world")
_PRODUCT_KEY = "all"


def _product(store, kind: str) -> VersionedProduct:
    """kind 白名单校验 + 门面构造（越界 kind 一律 400，不触碰文件系统）。"""
    if kind not in _PRODUCT_KINDS:
        raise HTTPException(status_code=400,
                            detail=f"不支持的产物类型：{kind}（支持 {', '.join(_PRODUCT_KINDS)}）")
    return VersionedProduct(store, kind)


@app.get("/api/projects/{project_id}/products/{kind}/versions")
def list_product_versions(project_id: str, kind: str, user: dict = Depends(_require_user)):
    """产物版本列表：versioned.list（含 words）+ 每版结算金额（账本反查）。"""
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
    return {"kind": kind, "versions": versions}


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
