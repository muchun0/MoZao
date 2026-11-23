# -*- coding: utf-8 -*-
"""账本提案（REQ-R30 M1 / US-5）：模型新增设定只能「提议」，未合并绝不生效。

数据流：
- 写章后 `detect_new_entities` 比对章末状态与世界观的已登记人物/物品，产出 add 提案；
- 提案落在 `proposals/<id>.json`，不被 memory / book_facts / 任何生成注入读取（隔离红线）；
- `merge_proposal` 按变更路径应用到 world/outline，走 `save_world/save_outline(commit=True,
  source="user")`——版本库上游比对天然把相关章节标 stale，不新增第二套过期机制。

失败一律显式抛给调用方（合并是用户动作，不能静默成功）；检测失败只降级留痕。
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from ..store.schemas import Outline, World
from .handover import append_audit

logger = logging.getLogger(__name__)

OPEN = "open"
MERGED = "merged"
REJECTED = "rejected"

_PATH_RE = re.compile(
    r"^(world|outline)\.([A-Za-z_][A-Za-z0-9_]*)(?:\[([^\]]+)\])?(?:\.([A-Za-z_][A-Za-z0-9_]*))?$")


def proposals_dir(store) -> Path:
    return Path(store.dir) / "proposals"


def proposal_path(store, pid: str) -> Path:
    return proposals_dir(store) / f"{pid}.json"


def _next_id(store) -> str:
    today = datetime.now().strftime("%Y%m%d")
    seq = 1
    while proposal_path(store, f"p-{today}-{seq:04d}").exists():
        seq += 1
    return f"p-{today}-{seq:04d}"


def _load(store, pid: str) -> dict | None:
    path = proposal_path(store, pid)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("提案读取失败: project=%s id=%s", store.dir.name, pid, exc_info=True)
        return None


def _open_exists(store, path: str) -> bool:
    return any(p.get("path") == path and p.get("status") == OPEN
               for p in list_proposals(store, status=OPEN, limit=200))


def create_proposal(store, *, task_id: str, path: str, op: str, old, new,
                    reason: str, evidence: dict | None = None) -> tuple[dict, bool]:
    """建提案（同路径已有 open 提案则复用）。返回 (提案, 是否新建)。"""
    if not _PATH_RE.match(str(path)):
        raise ValueError(f"非法变更路径: {path}")
    if op not in ("add", "set"):
        raise ValueError(f"未支持的操作: {op}")
    if _open_exists(store, path):
        for p in list_proposals(store, status=OPEN, limit=200):
            if p.get("path") == path:
                return p, False
    data = {
        "proposal_id": _next_id(store),
        "task_id": str(task_id),
        "path": str(path),
        "op": op,
        "old": old,
        "new": new,
        "reason": str(reason),
        "evidence": dict(evidence or {}),
        "status": OPEN,
        "created_at": time.time(),
        "resolved_at": None,
        "resolved_by": None,
    }
    store._write_json(proposal_path(store, data["proposal_id"]), data)
    append_audit(store, {"kind": "proposal_create", "proposal_id": data["proposal_id"],
                         "task_id": str(task_id), "path": path, "op": op})
    return data, True


def list_proposals(store, *, status: str = OPEN, limit: int = 50,
                   offset: int = 0) -> list[dict]:
    """只读列表（按创建时间倒序）。"""
    d = proposals_dir(store)
    rows: list[dict] = []
    if d.is_dir():
        for p in d.glob("p-*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                rows.append(data)
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: float(r.get("created_at") or 0), reverse=True)
    start = max(0, int(offset))
    return rows[start:start + max(1, min(200, int(limit or 50)))]


def detect_new_entities(store, cfg, task_id: str, chapter_no: int) -> list[dict]:
    """写章后检测：章末状态出现世界观未登记的人物/物品 → add 提案（不阻塞正文）。"""
    if not bool(getattr(cfg, "ledger_proposal", False)):
        return []
    try:
        state = store.load_chapter_state() or {}
        if state.get("chapter") and int(state.get("chapter") or 0) != int(chapter_no):
            return []
        world = store.load_world()
        if world is None:
            return []
        known_chars = {str(c.name).strip() for c in world.characters if str(c.name).strip()}
        known_items = {str(i.name).strip() for i in world.items if str(i.name).strip()}
        created: list[dict] = []
        for name, status in (state.get("characters") or {}).items():
            name = str(name).strip()
            if not name or name in known_chars:
                continue
            p, is_new = create_proposal(
                store, task_id=task_id, path=f"world.characters[{name}]", op="add",
                old=None,
                new={"name": name, "desc": f"写章新增（{str(status)[:40]}），待作者补全"},
                reason=f"第{chapter_no}章出现世界观未登记人物「{name}」",
                evidence={"ch": int(chapter_no), "state": str(status)[:120]})
            if is_new:
                created.append(p)
            if len(created) >= 5:
                break
        for name in (state.get("items") or {}):
            name = str(name).strip()
            if not name or name in known_items:
                continue
            p, is_new = create_proposal(
                store, task_id=task_id, path=f"world.items[{name}]", op="add",
                old=None,
                new={"name": name, "origin": "", "cost": "", "restriction": "", "effect": ""},
                reason=f"第{chapter_no}章出现世界观未登记物品「{name}」",
                evidence={"ch": int(chapter_no)})
            if is_new:
                created.append(p)
            if len(created) >= 8:
                break
        return created
    except Exception:
        logger.exception("提案检测失败（降级）: project=%s task=%s ch=%s",
                         store.dir.name, task_id, chapter_no)
        return []


def _find_index(container: list, key: str) -> int:
    if key.isdigit():
        idx = int(key)
        if 0 <= idx < len(container):
            return idx
        return -1
    for i, item in enumerate(container):
        if isinstance(item, dict) and str(item.get("name") or "") == key:
            return i
    return -1


def apply_path(root: dict, path: str, op: str, new) -> None:
    """把变更应用到 world/outline 的 dict 视图；非法路径抛 ValueError。"""
    m = _PATH_RE.match(path)
    if not m:
        raise ValueError(f"非法变更路径: {path}")
    root_key, attr, key, field = m.groups()
    if root_key not in root or not isinstance(root[root_key], dict):
        raise ValueError(f"路径根不存在: {root_key}")
    obj = root[root_key]
    if attr not in obj:
        raise ValueError(f"字段不存在: {root_key}.{attr}")
    container = obj[attr]
    if key is None:
        if op != "set":
            raise ValueError("缺少下标的路径只支持 set")
        obj[attr] = new
        return
    if isinstance(container, list):
        if op == "add":
            container.append(new)
            return
        idx = _find_index(container, key)
        if idx < 0:
            raise ValueError(f"列表项不存在: {key}")
        if field:
            if not isinstance(container[idx], dict):
                raise ValueError(f"列表项不是对象: {key}")
            container[idx][field] = new
        else:
            container[idx] = new
        return
    if isinstance(container, dict):
        if field:
            if not isinstance(container.get(key), dict):
                raise ValueError(f"键不是对象: {key}")
            container[key][field] = new
        else:
            container[key] = new
        return
    raise ValueError(f"字段不是列表/对象，无法按路径修改: {root_key}.{attr}")


def merge_proposal(store, pid: str, *, by: str = "") -> dict:
    """合并提案：应用到产物并提交新版本（source=user），标记 merged + 审计。"""
    data = _load(store, pid)
    if data is None:
        raise KeyError(f"提案不存在: {pid}")
    if data.get("status") != OPEN:
        raise ValueError(f"提案已处理: {data.get('status')}")
    path = str(data.get("path") or "")
    root_key = path.split(".", 1)[0]
    if root_key == "world":
        world = store.load_world()
        if world is None:
            raise ValueError("世界观不存在，无法合并提案")
        raw = {"world": world.to_dict()}
        apply_path(raw, path, str(data.get("op")), data.get("new"))
        obj = World.from_dict(raw["world"])
        v = store.save_world(obj, commit=True, source="user")
    elif root_key == "outline":
        outline = store.load_outline()
        if outline is None:
            raise ValueError("大纲不存在，无法合并提案")
        raw = {"outline": outline.to_dict()}
        apply_path(raw, path, str(data.get("op")), data.get("new"))
        obj = Outline.from_dict(raw["outline"])
        v = store.save_outline(obj, commit=True, source="user")
    else:
        raise ValueError(f"未知产物根: {root_key}")
    now = time.time()
    data["status"] = MERGED
    data["resolved_at"] = now
    data["resolved_by"] = str(by or "")
    data["result_version"] = v
    store._write_json(proposal_path(store, pid), data)
    append_audit(store, {"kind": "proposal_merge", "proposal_id": pid,
                         "task_id": data.get("task_id"), "path": path,
                         "version": v, "by": str(by or "")})
    return data


def reject_proposal(store, pid: str, *, by: str = "", reason: str = "") -> dict:
    """驳回提案：标记 rejected + 审计；不改任何产物。"""
    data = _load(store, pid)
    if data is None:
        raise KeyError(f"提案不存在: {pid}")
    if data.get("status") != OPEN:
        raise ValueError(f"提案已处理: {data.get('status')}")
    data["status"] = REJECTED
    data["resolved_at"] = time.time()
    data["resolved_by"] = str(by or "")
    data["reason"] = str(reason or "")
    store._write_json(proposal_path(store, pid), data)
    append_audit(store, {"kind": "proposal_reject", "proposal_id": pid,
                         "task_id": data.get("task_id"), "by": str(by or ""),
                         "reason": str(reason or "")})
    return data
