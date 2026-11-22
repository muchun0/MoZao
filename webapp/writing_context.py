# -*- coding: utf-8 -*-
"""写作参考侧栏只读聚合（REQ-R29）。

为什么需要本模块：章计划 + 出场人物匹配 + 按「当前写作章」计算的本章伏笔/五态计数，
无法用既有接口在前端低成本拼出（`/consistency/foreshadow` 按已写最大章计算；
`setting_cards` 是构建期索引，没有按章只读接口）。

纪律：只读、零 LLM、零写盘、不计费；伏笔状态一律复用
`consistency_report.build_foreshadow`（ReaderLedger 唯一源），前端只渲染不推导。
"""
from __future__ import annotations

from novel_gen.pipeline import consistency_report as report

from .logger import get_logger

logger = get_logger("writing_context")

__all__ = ["WritingContextError", "build_writing_context"]

#: 本章出场人物最多返回条数（侧栏速览，不做全量人物列表）
CHAR_LIMIT = 12

#: 参与人物匹配的章计划字段（都可能在写作时指向具体人物）
MATCH_FIELDS = ("goal", "core_event", "conflict", "character_moves", "foreshadowing")

#: 本章相关伏笔：临期/逾期需在本章处理；reveal_ch 命中本章同样算相关
CHAPTER_STATES = ("due_soon", "overdue")

#: 本章伏笔排序：越需要处理越靠前
_STATE_ORDER = {"overdue": 0, "due_soon": 1, "stalled": 2, "planted": 3, "revealed": 4}


class WritingContextError(ValueError):
    """入参非法（章号缺失等），由路由映射为 400。"""


def _load(store, attr: str, label: str):
    """统一读取入口：异常降级为 None 并留痕（非核心路径不允许裸吞）。"""
    try:
        return getattr(store, attr)()
    except Exception:
        logger.exception("写作侧栏读取%s失败（降级为空）", label)
        return None


def _chapter_plan(outline, ch: int) -> dict | None:
    if outline is None:
        return None
    for plan in getattr(outline, "chapters", None) or []:
        if int(getattr(plan, "no", 0) or 0) == ch:
            return plan.to_dict()
    return None


def _match_characters(world, plan: dict | None) -> list[dict]:
    """按章计划文本命中的世界观人物（名称直判，确定性零 LLM）。"""
    if world is None or not plan:
        return []
    blob = " ".join(str(plan.get(field) or "") for field in MATCH_FIELDS)
    if not blob.strip():
        return []
    hits = []
    for c in getattr(world, "characters", None) or []:
        name = str(getattr(c, "name", "") or "").strip()
        # 单字名误命中率过高（"王""李"），至少 2 字才参与匹配
        if len(name) < 2 or name not in blob:
            continue
        hits.append({
            "name": name,
            "role": getattr(c, "role", "") or "",
            "personality": getattr(c, "personality", "") or "",
            "desc": getattr(c, "desc", "") or "",
            "speech_style": getattr(c, "speech_style", "") or "",
        })
        if len(hits) >= CHAR_LIMIT:
            break
    return hits


def _foreshadow(store, cfg, ch: int, world) -> tuple[list[dict], dict, bool]:
    """返回 (本章相关伏笔, 五态计数, 账本是否可用)。失败/关闭均降级为空。"""
    counts = {k: 0 for k in ("planted", "due_soon", "overdue", "stalled", "revealed")}
    if world is None or (cfg is not None and not getattr(cfg, "reader_ledger", True)):
        return [], counts, False
    try:
        rows = report.build_foreshadow(store, cfg, chapter_no=ch)
    except Exception:
        logger.exception("写作侧栏计算伏笔状态失败（降级为空）: ch=%s", ch)
        return [], counts, False
    for row in rows:
        state = str(row.get("state") or "")
        if state in counts:
            counts[state] += 1
    chapter_rows = []
    for row in rows:
        reveal_ch = int(row.get("reveal_ch") or 0)
        if str(row.get("state")) in CHAPTER_STATES or reveal_ch == ch:
            chapter_rows.append({
                **row,
                # 距回收章数由后端算：0=本章到期，负数=已逾期，None=未登记回收章
                "reveal_in": (reveal_ch - ch) if reveal_ch > 0 else None,
            })
    chapter_rows.sort(key=lambda r: (
        _STATE_ORDER.get(str(r.get("state")), 9),
        r.get("reveal_ch") or 10 ** 6,
        str(r.get("name") or ""),
    ))
    return chapter_rows, counts, True


def build_writing_context(store, ch: int, cfg=None) -> dict:
    """组装写作侧栏「本章」视图 + 伏笔角标计数（单次只读请求）。"""
    try:
        ch = int(ch)
    except (TypeError, ValueError):
        raise WritingContextError("章号必须是整数")
    if ch < 1:
        raise WritingContextError("章号必须大于 0")

    if cfg is None:
        cfg = _load(store, "load_config", "项目配置")

    outline = _load(store, "load_outline", "大纲")
    world = _load(store, "load_world", "世界观")
    plan = _chapter_plan(outline, ch)
    items, counts, fs_ok = _foreshadow(store, cfg, ch, world)

    return {
        "ch": ch,
        "chapter": plan,
        "characters": _match_characters(world, plan),
        "foreshadow": {"items": items, "counts": counts},
        "available": {"outline": plan is not None, "world": world is not None,
                      "foreshadow": fs_ok},
    }
