# -*- coding: utf-8 -*-
"""章节「上游变更即失效标记」（档 1，只读、零 LLM、零写盘）。

数据来源唯一：`chapter_versions.json` 中每章「当前选中版本」的
`history[].upstream = {outline_v, world_v}`（生成/改写时由版本库自动记录）。
本模块不做任何落盘，也不新建 `staleness.json`——落盘会形成第二个权威源，
违反「跨模块可变状态收敛到单一权威源」的硬红线。

判定语义（作者视角）：
- fresh：当前选中版本的 upstream 与当前 outline/world 选中版本**完全一致**；
- stale：任一上游版本号不一致（落后或超前），reason 指明维度、差几版、变更时间；
- unknown：没有 upstream 记录 / 只有部分维度 / 当前上游版本不可读。
  存量章节必须落到 unknown，**不能**当作 fresh 给用户假的安全感。

档 1 只做「标记 + 可见」，不触发任何重写或自动传播（档 2 再做）。
"""
from __future__ import annotations

import logging

from ..store.versioned import VersionedProduct
from .consistency_report import chapter_numbers

logger = logging.getLogger(__name__)

#: 参与比对的上游产物（顺序即前端展示顺序）
UPSTREAM_KINDS = ("outline", "world")

#: 上游维度 → 作者可见名称
KIND_LABELS = {"outline": "大纲", "world": "世界观"}


def _book_id(store) -> str:
    """项目标识（仅用于日志）。"""
    return getattr(getattr(store, "dir", None), "name", "") or ""


def _ts(value) -> int | None:
    """历史版本 ts → 秒级时间戳；0/缺失（legacy 版本）一律 None，不冒充 1970 年。"""
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    return ts if ts > 0 else None


def _current_version(store, kind: str, data_gaps: list[str]) -> dict:
    """读取当前 outline/world 选中版本的 `{v, ts}`；失败降级为 v=None 并留痕。"""
    try:
        facade = VersionedProduct(store, kind)
        selected = facade.selected_version("all")
        if selected is None:
            return {"v": None, "ts": None}
        ts = None
        for item in facade.list("all"):
            if item.get("v") == selected:
                ts = _ts(item.get("ts"))
                break
        return {"v": selected, "ts": ts}
    except Exception:
        logger.warning("读取当前%s版本失败（本章判定降级为 unknown）: project=%s",
                       KIND_LABELS.get(kind, kind), _book_id(store), exc_info=True)
        data_gaps.append(f"读取当前{KIND_LABELS.get(kind, kind)}版本失败，相关章节判定为 unknown")
        return {"v": None, "ts": None}


def _selected_entry(rec: dict) -> dict | None:
    """章节版本索引 → 当前选中版本的历史记录（无则 None）。"""
    if not isinstance(rec, dict):
        return None
    selected = rec.get("selected")
    for item in rec.get("history") or []:
        if isinstance(item, dict) and item.get("v") == selected:
            return item
    return None


def _base_reason(outline: dict, world: dict) -> dict:
    """所有判定共用的基础字段（当前上游版本快照）。"""
    return {
        "outline_v": outline["v"],
        "world_v": world["v"],
        "based_on_outline_v": None,
        "based_on_world_v": None,
        "changed_at": None,
    }


def _compare(kind: str, based_on, current: dict) -> dict | None:
    """单维度比对：一致返回 None，否则返回差异明细（含方向与相差版本数）。"""
    if based_on == current["v"]:
        return None
    diff = int(current["v"]) - int(based_on)
    return {
        "based_on": int(based_on),
        "current": int(current["v"]),
        "diff": diff,                      # 正=落后（章节基于旧版），负=超前（当前选中更旧）
        "direction": "behind" if diff > 0 else "ahead",
        "changed_at": current["ts"],
    }


def _as_int(value) -> int:
    """宽松转 int（章号/版本号比较用）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _prev_dependency(upstream: dict, index: dict) -> tuple[str, list[dict]]:
    """章节级依赖（档2）比对：返回 (状态, 前文变更列表)。

    状态：recorded=有依赖记录；missing=存量章无该字段（必须判 unknown）；
    前文变更项：`{ch, based_on, current, direction}`，direction=rewritten（当前更新）
    或 reverted（当前被切回更旧版本）。无版本记录的存量章记 0，避免误报。
    """
    if "prev_chapters" not in upstream:
        return "missing", []
    refs = upstream.get("prev_chapters")
    if not isinstance(refs, list):
        return "unknown", []
    changed = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        ch = _as_int(ref.get("ch"))
        based = _as_int(ref.get("v"))
        if ch <= 0:
            continue
        rec = index.get(str(ch)) or {}
        cur = _as_int(rec.get("selected"))
        if cur == based:
            continue
        changed.append({
            "ch": ch,
            "based_on": based,
            "current": cur,
            "direction": "rewritten" if cur > based else "reverted",
        })
    return "recorded", changed


def _judge(no: int, rec: dict, outline: dict, world: dict, index: dict) -> dict:
    """单章判定：fresh / stale / unknown + 依据（两种成因可并存）。"""
    reason = _base_reason(outline, world)
    entry = _selected_entry(rec)
    if entry is None:
        reason["unknown_reason"] = "missing_selected_version"
        return {"chapter": no, "status": "unknown", "reason": reason}
    upstream = entry.get("upstream")
    if not isinstance(upstream, dict) or not upstream:
        reason["unknown_reason"] = "no_upstream_record"
        return {"chapter": no, "status": "unknown", "reason": reason}
    based_on = {k: upstream.get(f"{k}_v") for k in UPSTREAM_KINDS}
    reason["based_on_outline_v"] = based_on["outline"]
    reason["based_on_world_v"] = based_on["world"]
    if any(v is None for v in based_on.values()):
        reason["unknown_reason"] = "partial_upstream_record"
        return {"chapter": no, "status": "unknown", "reason": reason}
    if any(c["v"] is None for c in (outline, world)):
        reason["unknown_reason"] = "current_upstream_unavailable"
        return {"chapter": no, "status": "unknown", "reason": reason}

    # 档2：前文重写检测（存量章无该字段 → unknown，绝不判 fresh）
    prev_state, prev_changed = _prev_dependency(upstream, index)
    reason["prev_dependency"] = prev_state
    if prev_state == "missing":
        reason["unknown_reason"] = "no_prev_dependency_record"
        return {"chapter": no, "status": "unknown", "reason": reason}
    if prev_state == "unknown":
        reason["unknown_reason"] = "invalid_prev_dependency_record"
        return {"chapter": no, "status": "unknown", "reason": reason}
    if prev_changed:
        reason["prev_rewrites"] = prev_changed

    stale_dims = {}
    for kind in UPSTREAM_KINDS:
        diff = _compare(kind, based_on[kind], outline if kind == "outline" else world)
        if diff is not None:
            stale_dims[kind] = diff
    if not stale_dims and not prev_changed:
        return {"chapter": no, "status": "fresh", "reason": reason}
    if stale_dims:
        reason["stale_dims"] = stale_dims
    # stale_causes 让前端一眼分清「大纲/世界观变了」还是「前文被重写」
    causes = list(stale_dims.keys()) + (["prev_chapter"] if prev_changed else [])
    reason["stale_causes"] = causes
    # changed_at 取上游维度变更时间；前文重写没有稳定 ts（版本历史可查），不伪造
    stamps = [d["changed_at"] for d in stale_dims.values() if d["changed_at"]]
    reason["changed_at"] = max(stamps) if stamps else None
    return {"chapter": no, "status": "stale", "reason": reason}


def _ranges(numbers: list[int]) -> list[dict]:
    """连续章号压缩为区间，供前端生成「第 3–5、7 章」文案。"""
    out: list[dict] = []
    for no in numbers:
        if out and no == out[-1]["to"] + 1:
            out[-1]["to"] = no
        else:
            out.append({"from": no, "to": no})
    return out


def _causes(rows: list[dict]) -> list[dict]:
    """按上游维度汇总受影响章节（前端据此说明「因为大纲更新到 vN」）。"""
    causes = []
    for kind in UPSTREAM_KINDS:
        chapters = [r["chapter"] for r in rows
                    if r["status"] == "stale" and kind in (r["reason"].get("stale_dims") or {})]
        if chapters:
            causes.append({"kind": kind, "label": KIND_LABELS[kind], "chapters": chapters})
    return causes


def _prev_rewrite_summary(rows: list[dict]) -> list[dict]:
    """按「被重写的前文章」聚合受影响章节，供前端生成「第3章已重写，影响第4–6章」。"""
    agg: dict[tuple, list[int]] = {}
    for row in rows:
        for item in row["reason"].get("prev_rewrites") or []:
            key = (item["ch"], item["based_on"], item["current"], item["direction"])
            agg.setdefault(key, []).append(row["chapter"])
    return [{"ch": ch, "based_on": based, "current": cur,
             "direction": direction, "affected": sorted(affected)}
            for (ch, based, cur, direction), affected in sorted(agg.items())]


def build_chapter_staleness(store) -> dict:
    """构建全书章节过期标记（只读）。返回逐章判定 + 汇总，不抛异常。"""
    data_gaps: list[str] = []
    try:
        numbers = chapter_numbers(store)
    except Exception:
        logger.warning("扫描章节列表失败（按无章节处理）: project=%s", _book_id(store), exc_info=True)
        numbers, data_gaps = [], ["读取章节列表失败，本次已降级为空结果"]

    outline = _current_version(store, "outline", data_gaps)
    world = _current_version(store, "world", data_gaps)

    try:
        index = store.load_chapter_versions() or {}
    except Exception:
        logger.warning("读取章节版本索引失败（全部判定为 unknown）: project=%s",
                       _book_id(store), exc_info=True)
        index = {}
        data_gaps.append("读取章节版本索引失败，本章判定全部降级为 unknown")

    rows = [_judge(no, index.get(str(no)) or {}, outline, world, index) for no in numbers]
    stale = [r["chapter"] for r in rows if r["status"] == "stale"]
    unknown = [r["chapter"] for r in rows if r["status"] == "unknown"]
    fresh_count = len(rows) - len(stale) - len(unknown)

    if not rows:
        data_gaps.append("还没有章节正文，暂无可判定的章节")
    elif unknown:
        data_gaps.append(
            f"{len(unknown)} 章缺少生成时的上游版本记录，无法判断是否过期（不视为正常）")
        legacy = sum(1 for r in rows
                     if r["reason"].get("unknown_reason") == "no_prev_dependency_record")
        if legacy:
            data_gaps.append(
                f"其中 {legacy} 章产生于章节依赖记录上线前，无法判断前文是否被重写")

    return {
        "chapters": rows,
        "summary": {
            "total": len(rows),
            "fresh_count": fresh_count,
            "stale_count": len(stale),
            "unknown_count": len(unknown),
            "stale_chapters": stale,
            "unknown_chapters": unknown,
            "regenerate_from": stale[0] if stale else None,
            "ranges": _ranges(stale),
            "causes": _causes(rows),
            "prev_rewrites": _prev_rewrite_summary(rows),
            "current": {
                "outline_v": outline["v"],
                "world_v": world["v"],
                "outline_changed_at": outline["ts"],
                "world_changed_at": world["ts"],
            },
        },
        "data_gaps": data_gaps,
    }
