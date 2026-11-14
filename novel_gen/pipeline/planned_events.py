"""作者计划事件（REQ-R22 L3）：时间线上的可编辑卡片。

定位：
- 账本事件（events.jsonl）是「已经发生」的确定事实，只读投影；
- 计划事件是「作者想让它在某章发生」的设计意图，存 planned_events.json，
  只在写对应章时作为约束注入，绝不混进已发生事件流（避免污染一致性检查）。

数据形状（每条）::

    {"id": "pe_xxx", "title": "苏妄言发现灯壁刻字", "desc": "...",
     "ch": 15, "participants": ["苏妄言", "陆沉"], "kind": "planned",
     "status": "planned" | "done", "thread": "嵌在岩壁里的灯"}

`ch` 是目标章（1 起）；`thread` 可把卡片和某条伏笔连起来（作者自填，可空）。
"""
from __future__ import annotations

import re
import uuid
from typing import Iterable, Optional

MAX_ITEMS = 500
MAX_TITLE = 80
MAX_DESC = 600
MAX_PARTICIPANTS = 8
MAX_NAME = 24
KINDS = ("planned", "state", "object", "relationship", "foreshadow")
STATUS = ("planned", "done")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _text(v, limit: int) -> str:
    return str(v or "").strip()[:limit]


def normalize(items: Optional[Iterable[dict]]) -> list[dict]:
    """清洗并校验前端提交的计划事件；不合法抛 ValueError（由接口转 400）。"""
    if not isinstance(items, (list, tuple)):
        raise ValueError("items 必须是数组")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"计划事件最多 {MAX_ITEMS} 条（当前 {len(items)} 条）")
    out: list[dict] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("每条计划事件必须是对象")
        title = _text(raw.get("title"), MAX_TITLE)
        if not title:
            raise ValueError("计划事件的标题不能为空")
        try:
            ch = int(raw.get("ch") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"《{title}》的目标章不是数字")
        if not 1 <= ch <= 9999:
            raise ValueError(f"《{title}》的目标章必须在 1–9999")
        pid = _text(raw.get("id"), 40)
        if pid and not _ID_RE.match(pid):
            raise ValueError(f"《{title}》的 id 不合法")
        if not pid:
            pid = "pe_" + uuid.uuid4().hex[:10]
        if pid in seen:
            raise ValueError(f"计划事件 id 重复：{pid}")
        seen.add(pid)
        people = []
        for p in (raw.get("participants") or []):
            name = _text(p, MAX_NAME)
            if name and name not in people:
                people.append(name)
        if len(people) > MAX_PARTICIPANTS:
            raise ValueError(f"《{title}》的参与人最多 {MAX_PARTICIPANTS} 位")
        kind = _text(raw.get("kind"), 20) or "planned"
        if kind not in KINDS:
            kind = "planned"
        status = _text(raw.get("status"), 20) or "planned"
        if status not in STATUS:
            status = "planned"
        out.append({
            "id": pid,
            "title": title,
            "desc": _text(raw.get("desc"), MAX_DESC),
            "ch": ch,
            "participants": people,
            "kind": kind,
            "status": status,
            "thread": _text(raw.get("thread"), MAX_TITLE),
        })
    out.sort(key=lambda r: (r["ch"], r["title"]))
    return out


def from_store(store) -> list[dict]:
    """读项目里的计划事件；文件缺失/损坏返回空列表（不阻断视图）。"""
    if store is None:
        return []
    try:
        rows = store.load_planned_events()
    except Exception:
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("title")]


def render_block(items: list[dict], chapter_no: int) -> str:
    """渲染某一章的计划事件约束块；该章没有计划事件时返回空串（零 token）。"""
    rows = [r for r in (items or [])
            if _safe_int(r.get("ch")) == int(chapter_no) and r.get("title")]
    if not rows:
        return ""
    lines = ["## 本章作者计划事件（按作者的排期必须发生，不要把顺序打乱）"]
    for i, r in enumerate(rows, 1):
        people = "、".join(r.get("participants") or [])
        thread = str(r.get("thread") or "").strip()
        line = f"{i}. {r.get('title')}"
        if people:
            line += f"（参与：{people}）"
        if thread:
            line += f"｜关联线索：{thread}"
        desc = str(r.get("desc") or "").strip()
        if desc:
            line += f"\n   - 要点：{desc}"
        lines.append(line)
    return "\n".join(lines)


def _safe_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0
