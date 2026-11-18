# -*- coding: utf-8 -*-
"""内联生成记录（`inline_drafts.json`，R25 基座 §4.6）。

设计要点：
1. **幂等不依赖结算账本**：`tokens` 映射独立保存 `client_token → gen_id + charge_entry`
   并单独按 48h TTL 清理，因此结算账本 500 条淘汰不会造成重放二次扣费。
2. `items` 上限 50 条、保留 48h；读时惰性清理（只清内存，不因此写盘），
   写时原子落盘（`store._write_json` = 临时文件 + os.replace）。
3. 同章单飞：新生成把该章此前未采纳记录标 `invalidated_at`（已计费不退），
   采纳命中作废记录返回 410。
4. 并发：复用 `versioned` 的项目级 RLock，避免两处各自加锁破坏同一项目文件的一致性。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from .versioned import _project_lock

__all__ = [
    "RETENTION_SECONDS", "MAX_ITEMS", "load_state", "save_state",
    "find_by_token", "find_by_gen_id", "list_for_chapter",
    "upsert_generation", "invalidate_chapter", "mark_adopted", "new_gen_id",
]

RETENTION_SECONDS = 48 * 3600
MAX_ITEMS = 50
_FILE = "inline_drafts.json"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(text: str) -> float:
    """ISO 时间 → epoch 秒；解析失败按「已过期」处理（0）。"""
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _state_path(store):
    return store.dir / _FILE


def _empty_state() -> dict:
    return {"version": 1, "items": [], "tokens": {}}


def _normalize_state(raw: Optional[dict]) -> dict:
    """兼容缺字段/旧结构的文件：坏数据不抛错，按空结构继续（不阻塞读写）。"""
    if not isinstance(raw, dict):
        return _empty_state()
    items = raw.get("items")
    tokens = raw.get("tokens")
    return {
        "version": 1,
        "items": [i for i in items if isinstance(i, dict)] if isinstance(items, list) else [],
        "tokens": tokens if isinstance(tokens, dict) else {},
    }


def _prune(state: dict, now: float) -> dict:
    """惰性清理：过期 items、超过上限的最旧 items、过期 tokens。"""
    alive = []
    for item in state["items"]:
        if _parse_iso(item.get("expires_at")) > now:
            alive.append(item)
    alive = alive[-MAX_ITEMS:]
    tokens = {}
    for token, rec in (state.get("tokens") or {}).items():
        exp = rec.get("exp", 0) if isinstance(rec, dict) else 0
        if float(exp or 0) > now:
            tokens[token] = rec
    return {"version": 1, "items": alive, "tokens": tokens}


def load_state(store) -> dict:
    raw = store._read_json(_state_path(store), None)
    return _prune(_normalize_state(raw), time.time())


def save_state(store, state: dict) -> None:
    store._write_json(_state_path(store), state)


def new_gen_id() -> str:
    """生成记录 id：`ig_YYYYMMDD_<8 hex>`（时间可读 + 随机后缀防并发碰撞）。"""
    import os
    return f"ig_{time.strftime('%Y%m%d')}_{os.urandom(4).hex()}"


def find_by_token(store, token: str) -> Optional[dict]:
    if not token:
        return None
    return (load_state(store).get("tokens") or {}).get(str(token))


def find_by_gen_id(store, gen_id: str) -> Optional[dict]:
    for item in load_state(store)["items"]:
        if item.get("gen_id") == gen_id:
            return item
    return None


def _kind_of(item: dict) -> str:
    """记录类型：inline（续写/改写）或 branch（剧情走向）；旧记录无 kind 视为 inline。"""
    return str(item.get("kind") or "inline")


def list_for_chapter(store, ch_no: int, kind: str = "inline") -> list[dict]:
    """该章 48h 内、未采纳、未作废的记录（按创建时间倒序）；按 kind 分流。"""
    alive = [i for i in load_state(store)["items"]
             if int(i.get("ch") or 0) == int(ch_no)
             and _kind_of(i) == kind
             and not i.get("adopted") and not i.get("invalidated_at")]
    alive.sort(key=lambda i: _parse_iso(i.get("created_at")), reverse=True)
    return alive


def upsert_generation(store, item: dict) -> tuple[dict, bool]:
    """写入一条生成记录；同 `client_token` 已存在则回放原记录。

    Returns:
        (记录, 是否回放)。回放时不新增 item、不覆盖已有金额。
    """
    token = str(item.get("client_token") or "")
    now = time.time()
    with _project_lock(store.dir):
        state = _prune(_normalize_state(store._read_json(_state_path(store), None)), now)
        if token and token in state["tokens"]:
            gen_id = (state["tokens"][token] or {}).get("gen_id")
            for existing in state["items"]:
                if existing.get("gen_id") == gen_id:
                    return existing, True
        state["items"].append(item)
        state["items"] = state["items"][-MAX_ITEMS:]
        if token:
            state["tokens"][token] = {
                "gen_id": item["gen_id"],
                "charge_entry": item.get("charge_entry", ""),
                "exp": now + RETENTION_SECONDS,
            }
        save_state(store, state)
        return item, False


def invalidate_chapter(store, ch_no: int, kind: str = "inline") -> int:
    """把该章此前未采纳、未作废的记录标为作废（已计费不退），返回作废条数。

    默认只作用于 inline 记录；剧情走向（kind=branch）允许多批共存，不被内联作废。
    """
    now = time.time()
    changed = 0
    with _project_lock(store.dir):
        state = _prune(_normalize_state(store._read_json(_state_path(store), None)), now)
        for item in state["items"]:
            if (int(item.get("ch") or 0) == int(ch_no)
                    and _kind_of(item) == kind
                    and not item.get("adopted") and not item.get("invalidated_at")):
                item["invalidated_at"] = _iso(now)
                changed += 1
        if changed:
            save_state(store, state)
    return changed


def mark_adopted(store, gen_id: str, *, candidate_id: str, version: int,
                 client_token: str = "") -> Optional[dict]:
    """记录采纳结果（幂等：同 token 重复采纳返回已记录内容）。"""
    now = time.time()
    with _project_lock(store.dir):
        state = _prune(_normalize_state(store._read_json(_state_path(store), None)), now)
        target = None
        for item in state["items"]:
            if item.get("gen_id") == gen_id:
                target = item
                break
        if target is None:
            return None
        adopted = target.get("adopted")
        if isinstance(adopted, dict):
            same = adopted.get("client_token") == client_token
            if same or not client_token:
                return adopted
        target["adopted"] = {
            "candidate_id": candidate_id,
            "version": int(version),
            "client_token": client_token,
            "ts": _iso(now),
        }
        save_state(store, state)
        return target["adopted"]
