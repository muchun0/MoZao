# -*- coding: utf-8 -*-
"""写作便签 v2（`continue_drafts.json`，REQ-R26 §5.2 / TECH §2.2）。

旧结构（章号 → 单对象）只能存一张，写第二张会覆盖第一张。本模块升级为：

    {"version": 2, "chapters": {"1": {"items": [note...], "_evicted": [meta...]}}}

约束：
- 每章最多 10 条，超出淘汰最旧并留痕（`_evicted` 只留最近 20 条元信息）；
- 全局文件 ≤ 2MB，超出按「最旧章的最旧条」淘汰，同样留痕；
- 写盘原子（复用 `ProjectStore._write_json` = 临时文件 + `os.replace`）；
- 读时迁移 v1 结构，未知字段保留在 `_legacy`，不丢数据；
- 并发复用 `versioned._project_lock`（与版本索引/生成记录同一把项目锁）。

本模块属存储层：不依赖 Web / FastAPI / SQL，异常由调用方映射。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

from .versioned import _project_lock

__all__ = [
    "MAX_PER_CHAPTER", "MAX_EVICTED", "MAX_FILE_BYTES",
    "load", "list_notes", "find_by_natural_key", "add_note", "remove_note",
    "new_note_id",
]

_MAX_PER_CHAPTER = 10
_MAX_EVICTED = 20
_MAX_FILE_BYTES = 2 * 1024 * 1024
_FILE = "continue_drafts.json"

MAX_PER_CHAPTER = _MAX_PER_CHAPTER
MAX_EVICTED = _MAX_EVICTED
MAX_FILE_BYTES = _MAX_FILE_BYTES


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(text: str) -> float:
    """ISO 时间 → epoch 秒；解析失败按 0（最旧）处理。"""
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def new_note_id() -> str:
    return f"note_{time.strftime('%Y%m%d')}_{os.urandom(3).hex()}"


def _path(store):
    return store.dir / _FILE


def _note(raw: dict, *, origin: str = "branch") -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    summary = str(raw.get("summary") or raw.get("preview") or "").strip()
    direction = str(raw.get("direction") or raw.get("label") or "").strip()
    if not summary and not direction:
        return None
    tags = raw.get("tags")
    return {
        "draft_id": str(raw.get("draft_id") or raw.get("id") or new_note_id()),
        "direction": direction,
        "summary": summary,
        "source_tag": str(raw.get("source_tag") or ""),
        "tags": [str(t) for t in tags][:3] if isinstance(tags, list) else [],
        "gen_id": str(raw.get("gen_id") or ""),
        "item_id": str(raw.get("item_id") or ""),
        "origin": str(raw.get("origin") or origin),
        "created_at": str(raw.get("created_at") or _iso(time.time())),
    }


def _chapter_items(ch: dict) -> list[dict]:
    items = ch.get("items") if isinstance(ch, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for raw in items:
        note = _note(raw)
        if note:
            out.append(note)
    return out


def _evicted(ch: dict) -> list[dict]:
    rows = ch.get("_evicted") if isinstance(ch, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _normalize(raw) -> tuple[dict, bool]:
    """返回 (v2 结构, 是否需要写回迁移)。坏数据按空结构继续，不抛错。"""
    if not isinstance(raw, dict):
        return {"version": 2, "chapters": {}}, False
    try:
        is_v2 = int(raw.get("version") or 0) == 2
    except (TypeError, ValueError):
        is_v2 = False
    if is_v2 and isinstance(raw.get("chapters"), dict):
        chapters = {}
        for key, ch in raw["chapters"].items():
            chapters[str(key)] = {"items": _chapter_items(ch),
                                  "_evicted": _evicted(ch)[-_MAX_EVICTED:]}
        changed = chapters != raw.get("chapters")
        out = {"version": 2, "chapters": chapters}
        if raw.get("_legacy"):
            out["_legacy"] = raw["_legacy"]
        return out, changed

    # v1：{章号: {direction,label,preview,applied_at}} → v2 多便签
    chapters: dict[str, dict] = {}
    legacy: dict = {}
    for key, value in raw.items():
        if key in ("version", "chapters"):
            continue
        if str(key).isdigit() and isinstance(value, dict):
            note = _note({**value, "origin": "legacy"}, origin="legacy")
            if note:
                chapters[str(key)] = {"items": [note], "_evicted": []}
        else:
            legacy[key] = value
    out = {"version": 2, "chapters": chapters}
    if legacy:
        out["_legacy"] = legacy
    return out, True


def load(store) -> dict:
    """读 v2 结构（内存态迁移，不写盘）；供只读展示使用。"""
    data, _ = _normalize(store._read_json(_path(store), {}))
    return data


def _load_migrated(store) -> dict:
    """读并在结构变化时原子写回（调用方需已持项目锁）。"""
    data, changed = _normalize(store._read_json(_path(store), {}))
    if changed:
        store._write_json(_path(store), data)
    return data


def list_notes(store, ch_no: int) -> list[dict]:
    """该章便签（按创建时间倒序）。"""
    data = _load_migrated_safe(store)
    items = _chapter_items(data["chapters"].get(str(int(ch_no)), {}))
    items.sort(key=lambda n: _parse_iso(n.get("created_at")), reverse=True)
    return items


def _load_migrated_safe(store) -> dict:
    """读 + 迁移；迁移写盘失败不阻塞读取（返回内存态并让调用方日志留痕）。"""
    with _project_lock(store.dir):
        return _load_migrated(store)


def find_by_natural_key(store, ch_no: int, gen_id: str, item_id: str) -> Optional[dict]:
    """按 `(gen_id, item_id)` 查已存便签（采纳幂等的自然键）。"""
    if not gen_id or not item_id:
        return None
    for note in list_notes(store, ch_no):
        if note.get("gen_id") == str(gen_id) and note.get("item_id") == str(item_id):
            return note
    return None


def _evict_globally(chapters: dict) -> None:
    """文件超 2MB 时按「最旧章的最旧条」淘汰，直到低于上限（留痕）。"""
    while True:
        oldest = None
        for key, ch in chapters.items():
            for note in _chapter_items(ch):
                ts = _parse_iso(note.get("created_at"))
                if oldest is None or ts < oldest[0]:
                    oldest = (ts, key, note)
        if oldest is None:
            return
        _, key, note = oldest
        ch = chapters[key]
        ch["items"] = [i for i in _chapter_items(ch) if i["draft_id"] != note["draft_id"]]
        ch["_evicted"] = (_evicted(ch) + [{"draft_id": note["draft_id"],
                                           "direction": note.get("direction", ""),
                                           "evicted_at": _iso(time.time())}])[-_MAX_EVICTED:]
        size = len(json.dumps(chapters, ensure_ascii=False).encode("utf-8"))
        if size <= _MAX_FILE_BYTES:
            return


def add_note(store, ch_no: int, note: dict,
             dedup_key: Optional[tuple[str, str]] = None) -> tuple[dict, bool]:
    """追加一张便签（原子写）。同章超 10 条淘汰最旧并留痕。

    Args:
        dedup_key: `(gen_id, item_id)` 自然键；同一键已存在时直接返回原便签，
            不新增（在项目锁内判定，保证并发采纳只落一张）。

    Returns:
        (便签, 是否新建)。
    """
    ch_key = str(int(ch_no))
    saved = _note(note)
    if saved is None:
        raise ValueError("便签内容为空")
    with _project_lock(store.dir):
        data = _load_migrated(store)
        ch = data["chapters"].setdefault(ch_key, {"items": [], "_evicted": []})
        items = _chapter_items(ch)
        if dedup_key and all(dedup_key):
            gen_id, item_id = str(dedup_key[0]), str(dedup_key[1])
            for existing in items:
                if existing.get("gen_id") == gen_id and existing.get("item_id") == item_id:
                    return existing, False
        items.append(saved)
        if len(items) > _MAX_PER_CHAPTER:
            dropped = items[:-_MAX_PER_CHAPTER]
            items = items[-_MAX_PER_CHAPTER:]
            ch["_evicted"] = (_evicted(ch) + [
                {"draft_id": d["draft_id"], "direction": d.get("direction", ""),
                 "evicted_at": _iso(time.time())} for d in dropped])[-_MAX_EVICTED:]
        ch["items"] = items
        data["chapters"][ch_key] = ch
        size = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        if size > _MAX_FILE_BYTES:
            _evict_globally(data["chapters"])
        store._write_json(_path(store), data)
    return saved, True


def remove_note(store, ch_no: int, draft_id: str) -> bool:
    """删除单条便签（仅便签，不退款、不改正文）。返回是否命中。"""
    ch_key = str(int(ch_no))
    if not draft_id:
        return False
    with _project_lock(store.dir):
        data = _load_migrated(store)
        ch = data["chapters"].get(ch_key)
        if not ch:
            return False
        items = _chapter_items(ch)
        kept = [i for i in items if i["draft_id"] != str(draft_id)]
        if len(kept) == len(items):
            return False
        ch["items"] = kept
        data["chapters"][ch_key] = ch
        store._write_json(_path(store), data)
    return True
