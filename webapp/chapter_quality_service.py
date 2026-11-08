# -*- coding: utf-8 -*-
"""R14 单章重检服务：对指定版本复用 QualityChecker 的权威判定补做质检。

契约（与 webapp/routers/projects_chapters.py 的路由一致）：
- 幂等：版本已有 quality 且未显式 force → 直接返回既有结果，不调用模型；
- 失败态：模型/写盘失败显式抛出，绝不把空结果写成「质检通过」；
- 计费：不计平台按字数计费，但会消耗模型 token（前端需如实提示）。
"""
from __future__ import annotations

import logging

from novel_gen.pipeline.chapter_state import panel_baseline_dict
from novel_gen.pipeline.quality.checker import (
    QUALITY_TOTAL_BUDGET_S,
    QualityChecker,
    quality_snapshot,
)
from novel_gen.store.project import ProjectStore

logger = logging.getLogger("webapp.chapter_quality")

# 质检是用户点击后的交互请求：宁可快速失败让用户重试，也不让 SDK 长重试拖住请求。
# 单次超时 30s 覆盖评委正常延迟（reader 1200 tokens）；重试 0 次——连接失败/超时
# 立即返回 503，用户再点一次即重试（接口幂等、失败不写盘，重复调用无副作用）。
QUALITY_LLM_TIMEOUT = 30.0
QUALITY_LLM_MAX_RETRIES = 0
# 连接阶段单独限时：黑洞网络下 TCP 握手可能被系统层拖到 ~21s，5s 足以区分
# 「端点不可达」与「模型慢」，让连接类失败快速降级；响应阶段仍用 30s。
QUALITY_LLM_CONNECT_TIMEOUT = 5.0


def _quality_llm(pipeline):
    """构造质检专用 LLM：沿用项目同一 provider/model/key，但缩短超时、关闭 SDK 重试。

    不支持限时克隆的客户端（如 MockClient）原样复用，行为不变。
    """
    llm = getattr(pipeline, "llm", None)
    clone = getattr(llm, "clone_limited", None)
    if callable(clone):
        try:
            return clone(timeout=QUALITY_LLM_TIMEOUT, max_retries=QUALITY_LLM_MAX_RETRIES,
                         connect_timeout=QUALITY_LLM_CONNECT_TIMEOUT)
        except Exception:
            logger.warning("构建质检限时客户端失败，复用原客户端", exc_info=True)
    return llm


def _build_checker(pipeline, store: ProjectStore) -> QualityChecker:
    """复用生成链路同一权威实现；memory=None 避免重检污染小说级踩坑账本。"""
    return QualityChecker(
        pipeline.cfg, _quality_llm(pipeline), store,
        trace=getattr(pipeline, "trace", None),
        guard=getattr(pipeline, "guard", None),
        memory=None,
    )


def _chapter_plan(store: ProjectStore, no: int) -> tuple[str, str, str]:
    """取本章大纲计划（标题/一句话目标/核心事件），供质检对照；缺失时降级为空。"""
    try:
        outline = store.load_outline()
        if outline and 0 < no <= len(outline.chapters):
            p = outline.chapters[no - 1]
            return p.title or "", p.goal or "", getattr(p, "core_event", "") or ""
    except Exception:
        logger.warning("读取大纲失败，质检将跳过计划对照: chapter=%s", no, exc_info=True)
    return "", "", ""


def _panel_baseline(store: ProjectStore, no: int) -> dict:
    """机制数字基线：仅账本章号 < 当前章时可用（与 checker 内部同口径，防旧稿当承接）。"""
    try:
        st = store.load_chapter_state() or {}
        if int(st.get("chapter") or 0) < no:
            return panel_baseline_dict(st.get("numbers") or [])
    except Exception:
        logger.warning("读取机制账本失败，质检跳过数字方向校验: chapter=%s", no, exc_info=True)
    return {}


def _payload(no: int, version: int, quality: dict, reused: bool) -> dict:
    return {
        "no": no,
        "version": version,
        "reused": reused,
        "passed": bool(quality.get("passed")),
        "score": int(quality.get("score", 0) or 0),
        "issue_count": int(quality.get("issue_count", 0) or 0),
        "hook_score": quality.get("hook_score"),
        "quality": quality,
    }


def recheck_chapter_quality(store: ProjectStore, pipeline, no: int,
                            version: int = 0, force: bool = False) -> dict:
    """对第 no 章指定版本补做质检。

    version=0 时取当前选中版本（无 selected 则取最新版本）。
    force=False 且版本已有 quality 时不调用模型，直接复用（幂等）。
    """
    items = store.list_versions(no)
    if not items:
        raise ValueError(f"第 {no} 章还没有任何版本，无法质检")
    vmap = {int(x["v"]): x for x in items}
    v = int(version or 0)
    if not v:
        v = next((int(x["v"]) for x in items if x.get("selected")), 0) or max(vmap)
    if v not in vmap:
        raise ValueError(f"第 {no} 章不存在版本 v{v}")

    quality = vmap[v].get("quality")
    if quality and not force:
        logger.info("单章重检复用既有结果: project=%s chapter=%s v=%s",
                    store.dir.name, no, v)
        return _payload(no, v, quality, reused=True)

    if getattr(pipeline, "llm", None) is None:
        raise ValueError("项目未配置可用模型，无法质检")

    chapter = store.load_chapter_version(no, v)
    content = (getattr(chapter, "content", "") or "").strip() if chapter else ""
    if not content:
        raise ValueError(f"第 {no} 章 v{v} 正文为空，无法质检")

    title, goal, core_event = _chapter_plan(store, no)
    checker = _build_checker(pipeline, store)
    # 摘要传空：改写后旧摘要可能已过期（B 组暂缓），避免用旧摘要制造「摘要不一致」假阳性。
    # fail_closed=True：关键评委失败 → QualityUnavailable，此时尚未写任何盘（见 recheck）。
    # save_review=False：由本层按「先版本 quality、后 review.json」顺序落盘。
    # R16：交互式重检走有界并发（3 路）+ 整体 60s 墙钟上限；超时/连接失败按模型
    # 不可用 fail-closed，不写版本 quality、不写 review.json。
    result = checker.recheck(no, title, goal, content, summary="",
                             baseline=_panel_baseline(store, no),
                             core_event=core_event, save_review=False, fail_closed=True,
                             parallel=True, total_budget=QUALITY_TOTAL_BUDGET_S)
    snap = quality_snapshot(result, include_hook=True)
    if not store.update_version_quality(no, v, snap):
        raise RuntimeError(f"质检完成但写入版本 v{v} 失败（版本可能已被删除）")
    try:
        # 第二步写 review.json；失败则回滚版本 quality，避免「review 有新结果、版本 quality 为空」
        checker.persist_review(no, result, strict=True)
    except Exception:
        logger.error("review.json 写入失败，回滚版本 quality: chapter=%s v=%s", no, v,
                     exc_info=True)
        try:
            store.update_version_quality(no, v, quality)
        except Exception:
            logger.exception("回滚版本 quality 失败（需人工核对）: chapter=%s v=%s", no, v)
        raise
    logger.info("单章重检完成: project=%s chapter=%s v=%s score=%s issues=%s force=%s",
                store.dir.name, no, v, snap["score"], snap["issue_count"], force)
    return _payload(no, v, snap, reused=False)
