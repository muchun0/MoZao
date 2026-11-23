# -*- coding: utf-8 -*-
"""REQ-R24 P2：Plan 计划图的门 1/门 2 接口（约束卡编辑 + diff + impact 预览）。

设计边界：
- 只读写 `plan_graph.json`（Plan 是可改的计划层，不碰 Canon/账本）；
- 全部接口受 `plan_graph` 开关门控，关闭即 404（行为与未启用一致）；
- 非法输入一律 400 并说明是哪一条，不静默丢弃（门 1 手改的卡片价值高于批量容错）；
- diff 只读：不写任何账本，不改章节，不触发重生成。
"""
from ..core import *  # noqa: F401,F403  共享 app / 鉴权 / 项目定位
import logging

from fastapi import Body

logger = logging.getLogger(__name__)


def _plan_cfg(store):
    """项目配置 + 后台全局开关（与章节路由同一口径，读不到返回 None → 404）。"""
    try:
        cfg = store.load_config()
    except Exception:
        logger.exception("项目配置读取失败（Plan 接口按未开启处理）")
        return None
    try:
        apply_global_overrides(cfg)
    except Exception:
        logger.exception("全局开关覆盖注入失败（按项目快照判定）")
    return cfg


def _open(project_id: str, user: dict):
    _check_project_access(project_id, user)
    store = _find_project(project_id)
    cfg = _plan_cfg(store)
    if not bool(getattr(cfg, "plan_graph", False)):
        raise HTTPException(status_code=404, detail="开关 plan_graph 未开启（Plan 能力未启用）")
    return store, cfg


def _plan(store):
    from novel_gen.pipeline.state import plan_graph
    plan = plan_graph.load(store)
    if not plan:
        raise HTTPException(status_code=404, detail="本书还没有 Plan（先调用 /plan-graph/compile 编译）")
    return plan


def _canon(store, cfg):
    if not bool(getattr(cfg, "state_graph_v2", False)):
        return None
    try:
        from novel_gen.pipeline.state.graph import CanonGraph
        return CanonGraph(store, cfg).build()
    except Exception:
        logger.exception("Plan 接口建图失败（降级为只用 Plan 约束）")
        return None


def _foreshadowing(store, cfg):
    try:
        from novel_gen.pipeline.state.foreshadow import ForeshadowLedger
        return ForeshadowLedger(store, cfg).load()
    except Exception:
        logger.exception("伏笔账本读取失败（diff 的 must_resolve 降级）")
        return None


@app.get("/api/projects/{project_id}/plan-graph")
def get_plan_graph(project_id: str, view: str = "all", ch: int = 0, seed: str = "",
                   up_to: int = 0, user: dict = Depends(_require_user)):
    """Plan 只读视图：all（整份）/ cards（门 1 卡片）/ validate / diff / impact。

    diff 需要本章已写（scenes.json 有该章 state）；未写时返回 `has_observation=false`
    并给出空判定，前端提示「本章尚未生成，无法对比」。
    """
    store, cfg = _open(project_id, user)
    from novel_gen.pipeline.state import plan_graph
    view = (view or "all").strip()
    if view not in ("all", "cards", "validate", "diff", "impact"):
        raise HTTPException(status_code=400,
                            detail=f"未知 view: {view}（可选 all/cards/validate/diff/impact）")
    plan = _plan(store)
    if view == "validate":
        world = None
        try:
            world = store.load_world()
        except Exception:
            logger.exception("世界观读取失败（validate 的端点校验降级为只查 id）")
        return plan_graph.validate(plan, world)
    if view == "cards":
        return {"plan_version": plan.get("plan_version"), "ch_range": plan.get("ch_range"),
                "cards": plan_graph.cards(plan, ch=ch)}
    if view == "impact":
        if not seed.strip():
            raise HTTPException(status_code=400, detail="view=impact 需要 seed")
        return plan_graph.impact(plan, seed, ch=ch)
    if view == "diff":
        if ch <= 0:
            raise HTTPException(status_code=400, detail="view=diff 需要 ch")
        observed = plan_graph.observe(store, ch)
        return {**plan_graph.diff(plan, ch, observed,
                                  canon=_canon(store, cfg),
                                  foreshadowing=_foreshadowing(store, cfg)),
                "plan_version": plan.get("plan_version")}
    return plan


@app.post("/api/projects/{project_id}/plan-graph/compile")
def post_plan_compile(project_id: str, payload: dict = Body(default={}),
                      user: dict = Depends(_require_user)):
    """从大纲重新编译 Plan（零 LLM）；默认保留 `source=manual` 的人工卡片。"""
    store, cfg = _open(project_id, user)
    from novel_gen.pipeline.state import plan_graph
    try:
        outline = store.load_outline()
        world = store.load_world()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"大纲/世界观读取失败：{e}")
    if outline is None:
        raise HTTPException(status_code=400, detail="本书还没有大纲，无法编译 Plan")
    existing = plan_graph.load(store)
    keep = bool((payload or {}).get("keep_manual", True))
    plan = plan_graph.compile_from_outline(outline, world, keep_existing=keep,
                                           existing=existing)
    result = plan_graph.validate(plan, world)
    version = plan_graph.save(store, plan)
    return {"plan_version": version, "ch_range": plan.get("ch_range"),
            "counts": result["counts"], "ok": result["ok"],
            "issues": result["issues"]}


@app.put("/api/projects/{project_id}/plan-graph/constraints")
def put_plan_constraint(project_id: str, payload: dict = Body(...),
                        user: dict = Depends(_require_user)):
    """门 1 保存一张约束卡：按 id 覆盖或新建；写入后返回新版本与校验结论。"""
    store, cfg = _open(project_id, user)
    from novel_gen.pipeline.state import plan_graph
    plan = _plan(store)
    try:
        card = plan_graph.upsert_constraint(plan, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    world = None
    try:
        world = store.load_world()
    except Exception:
        logger.exception("世界观读取失败（保存后校验降级为只查 id）")
    check = plan_graph.validate(plan, world)
    version = plan_graph.save(store, plan)
    return {"plan_version": version, "constraint": card, "ok": check["ok"],
            "issues": check["issues"]}


@app.delete("/api/projects/{project_id}/plan-graph/constraints/{cid}")
def delete_plan_constraint(project_id: str, cid: str, user: dict = Depends(_require_user)):
    """门 1 删除一张约束卡。"""
    store, cfg = _open(project_id, user)
    from novel_gen.pipeline.state import plan_graph
    plan = _plan(store)
    if not plan_graph.remove_constraint(plan, cid):
        raise HTTPException(status_code=404, detail=f"约束不存在：{cid}")
    version = plan_graph.save(store, plan)
    return {"plan_version": version, "removed": cid}


@app.post("/api/projects/{project_id}/plan-graph/impact-preview")
def post_plan_impact_preview(project_id: str, payload: dict = Body(...),
                             user: dict = Depends(_require_user)):
    """门 2 保存 Base 前的影响预览：一次给 Plan 侧 + Canon 侧的受影响章。

    `state_graph_v2` 未开时只答 Plan 侧（`canon_available=false`），不报错。
    """
    store, cfg = _open(project_id, user)
    from novel_gen.pipeline.state import plan_graph
    plan = plan_graph.load(store) or {}
    entities = [str(x).strip() for x in ((payload or {}).get("entities") or []) if str(x).strip()]
    if not entities:
        raise HTTPException(status_code=400, detail="entities 不能为空（传改动的实体名列表）")
    canon = _canon(store, cfg)
    rows = []
    all_ch: set[int] = set()
    for name in entities[:50]:
        p = plan_graph.impact(plan, name)
        c = None
        if canon is not None:
            try:
                from novel_gen.pipeline.state.graph import CanonGraph
                c = CanonGraph(store, cfg).impact(entity=name)
            except Exception:
                logger.exception("Canon impact 计算失败（本次只回 Plan 侧影响）")
        chapters = sorted(set(p.get("chapters") or []) |
                          set((c or {}).get("chapters") or []))
        all_ch |= set(chapters)
        rows.append({"entity": name,
                     "plan_chapters": p.get("chapters") or [],
                     "canon_chapters": (c or {}).get("chapters") or [],
                     "chapters": chapters,
                     "foreshadows": (c or {}).get("foreshadows") or []})
    return {"enabled": True, "entities": rows, "chapters": sorted(all_ch),
            "canon_available": canon is not None,
            "counts": {"entities": len(rows), "chapters": len(all_ch)}}
