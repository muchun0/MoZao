# -*- coding: utf-8 -*-
"""双写对账（REQ-R21 US-9 / TECH-R21 §2.4）。

切换期旧文件（`foreshadow_commitment.json`）与新文件（`foreshadowing.json`）同写，
每次 commit 后跑一次对账：比对 `status`、`due_ch`、`evidence` 三项，差异写入
`reconcile_report.json` 并计数；**连续 3 章零差异**才允许关闭旧写入。

事件侧只比对条数与章号分布，不比对措辞（措辞由抽取质量决定，属于已知限制）。
"""
from __future__ import annotations

import logging

from .events import EventStream
from .foreshadow import status_for

logger = logging.getLogger(__name__)

#: 允许关闭旧写入所需的连续零差异章数
ZERO_DIFF_STREAK = 3

_FIELDS = ("status", "due_ch", "evidence")


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _name(item) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or "").strip()
    return str(getattr(item, "name", "") or "").strip()


def _field(item, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def reconcile(store, at_ch: int = 0) -> dict:
    """比对两份伏笔文件 + 事件条数；返回报告并落 `reconcile_report.json`。"""
    try:
        old = store.load_foreshadow_commitment() or {}
    except Exception:
        old = {}
    old_items = {}
    for it in ((old.get("items") if isinstance(old, dict) else None) or []):
        n = _name(it)
        if n:
            old_items[n] = it

    try:
        new = store.load_foreshadowing() or {}
    except Exception:
        new = {}
    new_items = [it for it in (new.get("items") if isinstance(new, dict) else []) or []
                 if isinstance(it, dict)]

    diffs = []
    checked = 0
    for it in new_items:
        name = str(it.get("name") or "").strip()
        o = old_items.get(name)
        if o is None:
            continue
        checked += 1
        revealed = _int(_field(o, "revealed_ch", 0), 0)
        expected_status = "resolved" if revealed else status_for(it, at_ch)
        actual_status = str(it.get("status") or "")
        if actual_status != expected_status:
            diffs.append({"name": name, "field": "status",
                          "old": expected_status, "new": actual_status})
        old_due = _int(_field(o, "due_ch", 0), 0)
        if old_due and old_due != _int(it.get("due_ch"), 0):
            diffs.append({"name": name, "field": "due_ch",
                          "old": old_due, "new": _int(it.get("due_ch"), 0)})
        evidence = it.get("evidence") or {}
        old_ev_ch = revealed
        new_ev_ch = _int((evidence or {}).get("ch"), 0)
        if old_ev_ch != new_ev_ch:
            diffs.append({"name": name, "field": "evidence",
                          "old": old_ev_ch, "new": new_ev_ch})

    # 事件：只比条数与章号分布
    try:
        events = EventStream(store).all()
    except Exception:
        events = []
    try:
        mem = store.load_book_memory() or {}
    except Exception:
        mem = {}
    mem_events = ((mem.get("events") or {}).get("consumed") or []) if isinstance(mem, dict) else []
    ev_chs = sorted({_int(e.get("ch")) for e in events})
    mem_chs = sorted({_int(e.get("ch")) for e in mem_events if isinstance(e, dict)})

    prev = {}
    path = store.reconcile_report_path()
    if path.exists():
        try:
            import json
            prev = json.loads(path.read_text(encoding="utf-8")) or {}
        except Exception:
            prev = {}
    streak = _int(prev.get("zero_diff_streak"), 0)
    streak = streak + 1 if not diffs else 0

    report = {
        "version": 1,
        "at_ch": int(at_ch or 0),
        "checked": checked,
        "diffs": diffs[:200],
        "diff_count": len(diffs),
        "zero_diff_streak": streak,
        "can_disable_legacy_write": streak >= ZERO_DIFF_STREAK,
        "events": {"new_rows": len(events), "legacy_rows": len(mem_events),
                   "new_chs": ev_chs, "legacy_chs": mem_chs},
    }
    try:
        store.save_reconcile_report(report)
    except Exception:
        logger.exception("对账报告落盘失败（本次结果仍返回，不写章不中断）")
    if diffs:
        logger.warning("双写对账发现 %d 处差异（不关闭旧写入）", len(diffs))
    return report
