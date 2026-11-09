# -*- coding: utf-8 -*-
"""R1「按此改」装配层：单条改稿建议 → 定点局部改写 → 新版本落盘 + 结算。

router 只做鉴权与 HTTP；本模块负责：
1. 从 review.json 取该条 issue 的完整字段（evidence/fix_suggestion 未被版本快照截断）；
2. 调用 novel_gen 的定点改写（只替换命中段落，窗口外逐字符不变）；
3. 走既有「章节版本」口径落盘与结算（新版本按版本字数计费，BYOK 不扣费）。

设计取舍：不新建计费口径、不做整章兜底。定位失败直接报错，避免借「按此改」
之名改动用户未选中的正文。
"""
from __future__ import annotations

import json

from novel_gen.pipeline.book_memory import BookMemory
from novel_gen.pipeline.chapter_ledger import sync_after_rewrite
from novel_gen.pipeline.quality.issue_rewrite import rewrite_issue
from novel_gen.store.schemas import Chapter
from novel_gen.store.project import ProjectStore

from .logger import get_logger
from .settle_ledger import snapshot_chapter_versions, settle_chapter_versions

logger = get_logger("chapter_issue")

__all__ = ["load_chapter_issues", "resolve_issue", "apply_issue_rewrite"]


def load_chapter_issues(store: ProjectStore, no: int) -> list[dict]:
    """读取 review.json 中该章的完整 issue 列表（含 evidence/fix_suggestion）。"""
    path = store.dir / "review.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("读取 review.json 失败（按无记录处理）: project=%s no=%d",
                       store.dir.name, no)
        return []
    if not isinstance(data, list):
        return []
    for rec in reversed(data):
        if int(rec.get("chapter", 0)) == no:
            issues = rec.get("issues") or []
            return [it for it in issues if isinstance(it, dict)]
    return []


def _detail_matches(full: str, brief: str) -> bool:
    """版本快照的 detail 被截断到 120 字，按前 60 字做前缀匹配。"""
    full, brief = (full or "").strip(), (brief or "").strip()
    if not brief:
        return True
    return full.startswith(brief[:60]) or brief.startswith(full[:60])


def resolve_issue(store: ProjectStore, no: int, payload: dict) -> dict | None:
    """把前端提交的 issue 标识对齐到 review.json 的完整记录。

    版本快照里的字段是截断副本，局部改写需要完整 evidence/fix_suggestion；
    匹配不到（旧项目/记录被清理）时回落到请求体字段，仍缺关键字段则返回 None。
    """
    issue_type = str(payload.get("type") or "").strip()
    detail = str(payload.get("detail") or "").strip()
    evidence = str(payload.get("evidence") or "").strip()

    best: dict | None = None
    for it in load_chapter_issues(store, no):
        if issue_type and str(it.get("type") or "") != issue_type:
            continue
        if not _detail_matches(str(it.get("detail") or ""), detail):
            continue
        if evidence and str(it.get("evidence") or "") == evidence:
            return it
        if best is None:
            best = it
    if best is not None:
        return best
    if detail or evidence:
        return {"level": payload.get("level", ""), "type": issue_type,
                "detail": detail, "evidence": evidence,
                "fix_suggestion": payload.get("fix_suggestion", ""),
                "pos": payload.get("pos", "")}
    return None


def _chapter_plan(store: ProjectStore, no: int, chapter: Chapter) -> tuple[str, str]:
    """取本章大纲标题/目标（供重写 prompt）；无大纲时回落章节自身标题。"""
    try:
        outline = store.load_outline()
        if outline and 1 <= no <= len(outline.chapters):
            plan = outline.chapters[no - 1]
            return plan.title or chapter.title, plan.goal or ""
    except Exception:
        logger.warning("读取大纲失败（用章节标题兜底）: project=%s no=%d",
                       store.dir.name, no)
    return chapter.title, ""


def apply_issue_rewrite(store: ProjectStore, pipeline, chapter: Chapter,
                        from_version: int, payload: dict, *,
                        billing_mode: str, project_id: str) -> dict:
    """执行「按此改」：局部改写 → 保存新版本（设为当前）→ 结算新增版本。

    Returns: {no, from_version, version, span, rewritten_chars, charged, due}
    Raises: ValueError（定位失败 / 模型无结果 / 内容未变化），由 router 转 4xx。
    """
    no = chapter.no
    issue = resolve_issue(store, no, payload)
    if issue is None:
        raise ValueError("未找到对应的改稿建议，请刷新版本列表后重试")

    cfg = pipeline.cfg
    plan_title, plan_goal = _chapter_plan(store, no, chapter)
    memory = None
    if getattr(cfg, "book_memory", True):
        try:
            memory = BookMemory(store, cfg)
        except Exception:
            logger.warning("BookMemory 初始化失败（降级为无记忆注入）: project=%s no=%d",
                           store.dir.name, no, exc_info=True)

    result = rewrite_issue(
        cfg, pipeline.llm, store, no, chapter.content, issue,
        plan_title=plan_title, plan_goal=plan_goal,
        trace=pipeline.trace, guard=pipeline.guard, memory=memory)
    if not result.get("ok"):
        raise ValueError(result.get("reason") or "局部改写失败，原文未改动")

    chapter.content = result["content"]
    # 保存 + 结算放在同一临界区：避免与版本切换/删除并发时版本号快照错位
    from . import core
    with core._LOCK:
        before = snapshot_chapter_versions(store, no)
        new_v = store.save_chapter(chapter, source="rewrite", promote=True)
        if new_v is None:
            raise ValueError("改写结果与原文相同，未产生新版本")
        settles = settle_chapter_versions(store, no, before, mode=billing_mode,
                                          project_id=project_id)

    charged = round(sum(float(s.get("charged") or 0) for s in settles), 2)
    due = round(sum(float(s.get("due") or 0) for s in settles), 2)
    # 结算之后的独立步骤：同步本章派生账本（摘要/场景标脏/机制账本/章末状态）。
    # 放在临界区之外（避免持锁跑 LLM），失败只留日志、不回滚已结算版本。
    ledger = {"status": "skipped"}
    try:
        ledger = sync_after_rewrite(store, pipeline, no, version=new_v)
    except Exception:
        logger.exception("按此改账本同步异常（正文与结算已保留）: project=%s no=%d",
                         store.dir.name, no)
    logger.info("按此改: project=%s no=%d from_v=%d new_v=%d span=%s rewrite_chars=%d "
                "charged=%.2f mode=%s ledger=%s",
                store.dir.name, no, from_version, new_v, result["span"],
                result["rewritten_chars"], charged, billing_mode,
                ledger.get("commit"))
    return {"no": no, "from_version": from_version, "version": new_v,
            "span": list(result["span"]), "rewritten_chars": result["rewritten_chars"],
            "charged": charged, "due": due, "ledger": ledger}
