# -*- coding: utf-8 -*-
"""生成前章节一致性门禁（档 3，纯函数、零 LLM、毫秒级）。

判定口径（写死，改动需同步 decision 与单测）：
设本次要写的章集合为 T，全书 stale 章集合为 S：
- 仅当存在 c ∈ S 且 c < max(T) 且 c ∉ T 时拒绝（目标章之前有未纳入本次的过期章）；
- 从过期章起向后重写（T 覆盖 S 的相关章）放行；
- unknown 永不参与拦截（只提示）。

本模块不读章节正文（避免把毫秒级守卫变成秒级 IO）：只读大纲章数、
`chapter_versions.json` 版本索引与版本库里的 outline/world 版本。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .chapter_staleness import build_chapter_staleness
from .consistency_report import chapter_numbers

logger = logging.getLogger(__name__)

#: 门禁只对写章类 agent 生效
WRITER_AGENT = "writer.chapter"


def _ranges(nos: list[int]) -> list[list[int]]:
    """连续章号压缩：[[4, 5], [8, 9]]。"""
    out: list[list[int]] = []
    for n in sorted(set(nos)):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return out


def resolve_target_chapters(store, params: dict) -> list[int]:
    """按 scope 解析本次目标章集合（只读大纲与已写章文件名，不加载正文）。"""
    outline = store.load_outline()
    total = len(outline.chapters) if outline and outline.chapters else 0
    if total <= 0:
        return []
    params = params or {}
    scope = params.get("scope")
    if scope == "single":
        no = int(params.get("ch_no") or 0)
        return [no] if 0 < no <= total else []
    if scope == "range":
        start = max(1, int(params.get("from_ch") or 1))
        end = min(int(params.get("to_ch") or total), total)
        return list(range(start, end + 1)) if end >= start else []
    done = set(chapter_numbers(store))
    return [n for n in range(1, total + 1) if n not in done]


def _filter_detail(summary: dict, blocking: list[int]) -> dict:
    """把 summary 的成因收窄到真正造成拦截的章，避免提示无关章。"""
    keep = set(blocking)
    causes = []
    for item in summary.get("causes") or []:
        chapters = [c for c in (item.get("chapters") or []) if c in keep]
        if chapters:
            causes.append({**item, "chapters": chapters})
    rewrites = []
    for item in summary.get("prev_rewrites") or []:
        affected = [c for c in (item.get("affected") or []) if c in keep]
        if affected:
            rewrites.append({**item, "affected": affected})
    if not causes and summary.get("causes"):
        causes = summary["causes"]
    if not rewrites and summary.get("prev_rewrites"):
        rewrites = summary["prev_rewrites"]
    return {"causes": causes, "prev_rewrites": rewrites}


def guard_staleness(
    store,
    params: dict,
    *,
    cfg: Optional[Any] = None,
    agent: str = WRITER_AGENT,
    allow_inconsistent: bool = False,
) -> dict:
    """生成前一致性门禁。

    返回 `{"blocked": bool, "detail": {...}, "unstable_chapters": [...]}`：
    - blocked=True：调用方应返回 409（detail 含受影响区间/两种成因/两个出口）；
    - blocked=False：允许生成；`unstable_chapters` 为本次需要标注的过期章
      （仅在 allow_inconsistent=True 时有值）。
    任何内部异常都降级放行并 warning 留痕（门禁是提示性能力，不能阻断主链路）。
    """
    allowed = {"blocked": False, "detail": {}, "unstable_chapters": []}
    if agent != WRITER_AGENT:
        return allowed
    try:
        cfg = cfg or store.load_config()
        if not getattr(cfg, "stale_gate", True):
            return allowed
        data = build_chapter_staleness(store)
        summary = data.get("summary") or {}
        stale = [int(c) for c in (summary.get("stale_chapters") or [])]
        if not stale:
            return allowed
        targets = resolve_target_chapters(store, params)
        if allow_inconsistent:
            # 已知不一致下生成：不再拦截，把过期章号交给记忆装配做标注
            return {"blocked": False, "detail": {}, "unstable_chapters": stale}
        if not targets:
            return allowed
        max_t = max(targets)
        in_range = set(targets)
        blocking = [c for c in stale if c < max_t and c not in in_range]
        if not blocking:
            return allowed
        narrowed = _filter_detail(summary, blocking)
        ranges = _ranges(blocking)
        # total_chapters / est_words 仅供前端「从第 X 章起重写」的预估提示（不参与判定）
        total = len(store.load_outline().chapters or [])
        per_ch = int(getattr(cfg, "chapter_words", 2500) or 2500)
        from_ch = blocking[0]
        detail = {
            "code": "stale_gate",
            "message": "目标章之前有更早的章节已过期：直接续写会和旧设定对不上。"
                       "可以已知不一致继续，或从过期章起重写。",
            "stale_chapters": blocking,
            "ranges": ranges,
            "regenerate_from": from_ch,
            "total_chapters": total,
            "est_words": max(0, total - from_ch + 1) * per_ch,
            "causes": narrowed["causes"],
            "prev_rewrites": narrowed["prev_rewrites"],
            "options": ["continue", "rewrite_from"],
        }
        logger.info("一致性门禁拦截: project=%s targets=%s blocking=%s",
                    getattr(getattr(store, "dir", None), "name", ""),
                    targets, blocking)
        return {"blocked": True, "detail": detail, "unstable_chapters": []}
    except Exception:
        logger.warning("一致性门禁执行失败（降级放行）: params=%s", params, exc_info=True)
        return allowed
