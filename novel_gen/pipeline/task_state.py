# -*- coding: utf-8 -*-
"""任务状态机（REQ-R30 M0）：`runs/<task_id>/state.json` 是任务语义的唯一权威源。

红线对齐（AGENTS.md §3）：
- 单一权威源：状态/进度/待审信息只在这里写；`task.json` 仅为前端投影（progress）。
- 非法流转必须拒绝并留痕（`state_rejections.jsonl`），不允许静默改状态。
- 全部写盘原子（store._write_json：tmp + os.replace）。
- 状态写入串行化（进程内按项目加锁），避免生成线程与 API 并发覆盖。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

QUEUED = "queued"
RUNNING = "running"
PENDING_REVIEW = "pending_review"
DONE = "done"
FAILED = "failed"
ABORTED = "aborted"

STATES = (QUEUED, RUNNING, PENDING_REVIEW, DONE, FAILED, ABORTED)

# 合法流转表：终态 aborted 不再接受任何自动流转
ALLOWED: dict[str, set[str]] = {
    QUEUED: {RUNNING, ABORTED},
    RUNNING: {PENDING_REVIEW, DONE, FAILED, ABORTED},
    PENDING_REVIEW: {RUNNING, ABORTED},
    DONE: {QUEUED},
    FAILED: {QUEUED},
    ABORTED: set(),
}

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class TaskStateError(ValueError):
    """非法状态流转（调用方应捕获并向用户返回 409/400）。"""


def _lock_for(project_dir: Path) -> threading.RLock:
    key = str(project_dir)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def runs_dir(store) -> Path:
    return Path(store.dir) / "runs"


def state_path(store, task_id: str) -> Path:
    return runs_dir(store) / str(task_id) / "state.json"


def rejection_path(store, task_id: str) -> Path:
    return runs_dir(store) / str(task_id) / "state_rejections.jsonl"


def load_state(store, task_id: str) -> dict | None:
    """读 state.json；缺失/损坏返回 None（调用方按开关回退）。"""
    path = state_path(store, task_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("state.json 读取失败: project=%s task=%s", store.dir.name, task_id,
                       exc_info=True)
        return None


def save_state(store, state: dict) -> None:
    """原子写 state.json（调用方必须持有项目锁）。"""
    state["updated_at"] = time.time()
    store._write_json(state_path(store, state["task_id"]), state)


def create_state(store, manifest: dict, *, state: str = QUEUED) -> dict:
    """建 state（幂等：已存在则原样返回，不覆盖历史）。"""
    task_id = str(manifest.get("task_id") or "")
    if not task_id:
        raise TaskStateError("manifest 缺少 task_id")
    if state not in STATES:
        raise TaskStateError(f"未知状态: {state}")
    with _lock_for(Path(store.dir)):
        existing = load_state(store, task_id)
        if existing is not None:
            return existing
        data = dict(manifest)
        data.update({
            "state": state,
            "attempt": 1,
            "created_at": float(manifest.get("created_at") or time.time()),
            "updated_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "progress": {},
            "pending_handover_ids": [],
            "resume": {"remaining_chapters": []},
            "history": [],
        })
        save_state(store, data)
        return data


def transition(store, task_id: str, to: str, *, by: str = "system",
               reason: str = "", force: bool = False) -> dict:
    """状态流转：合法才写；非法抛 TaskStateError 并留痕（force 仅供测试/人工纠偏）。"""
    if to not in STATES:
        raise TaskStateError(f"未知状态: {to}")
    with _lock_for(Path(store.dir)):
        data = load_state(store, task_id)
        if data is None:
            raise TaskStateError(f"任务状态不存在: {task_id}")
        current = str(data.get("state") or "")
        if current == to:
            return data
        if not force and to not in ALLOWED.get(current, set()):
            _record_rejection(store, task_id, current, to, by, reason)
            logger.warning("非法状态流转已拒绝: project=%s task=%s %s→%s by=%s",
                           store.dir.name, task_id, current, to, by)
            raise TaskStateError(f"非法状态流转: {current} → {to}")
        now = time.time()
        data["state"] = to
        data["updated_at"] = now
        if to == RUNNING and not data.get("started_at"):
            data["started_at"] = now
        if to in (DONE, FAILED, ABORTED):
            data["finished_at"] = now
        data.setdefault("history", []).append(
            {"from": current, "to": to, "at": now, "by": str(by), "reason": str(reason)})
        save_state(store, data)
        return data


def update_progress(store, task_id: str, **kw) -> dict | None:
    """更新进度（唯一写入口）；state 不存在返回 None，由调用方回退旧逻辑。"""
    with _lock_for(Path(store.dir)):
        data = load_state(store, task_id)
        if data is None:
            return None
        prog = dict(data.get("progress") or {})
        prog.update(kw)
        data["progress"] = prog
        save_state(store, data)
        return data


def mark_pending(store, task_id: str, *, handover_ids: list[str] | None = None,
                 remaining_chapters: list[int] | None = None,
                 by: str = "system", reason: str = "") -> dict:
    """转待审：同时记录续跑所需的剩余章号（放行时据此续跑，不重写已完成章）。"""
    with _lock_for(Path(store.dir)):
        data = transition(store, task_id, PENDING_REVIEW, by=by, reason=reason)
        data["pending_handover_ids"] = [str(x) for x in (handover_ids or [])]
        data["resume"] = {"remaining_chapters": [int(x) for x in (remaining_chapters or [])]}
        save_state(store, data)
        return data


def clear_pending(store, task_id: str) -> dict:
    """放行：清空待审工单列表（状态由 transition 负责）。"""
    with _lock_for(Path(store.dir)):
        data = load_state(store, task_id)
        if data is None:
            raise TaskStateError(f"任务状态不存在: {task_id}")
        data["pending_handover_ids"] = []
        data["resume"] = {"remaining_chapters": []}
        save_state(store, data)
        return data


def task_id_from_task_json(store) -> str:
    """从 task.json 读当前任务 id（身份投影；读不到返回空串）。"""
    try:
        path = Path(store.dir) / "task.json"
        if not path.exists():
            return ""
        meta = json.loads(path.read_text(encoding="utf-8"))
        return str((meta or {}).get("task_id") or "")
    except Exception:
        return ""


def project_now(store, task_id: str) -> dict | None:
    """把 state 投影到 task.json（只写身份/进度/待审标记，不改变既有字段）。"""
    with _lock_for(Path(store.dir)):
        data = load_state(store, task_id)
        if data is None:
            return None
        path = Path(store.dir) / "task.json"
        try:
            meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not isinstance(meta, dict):
                meta = {}
        except Exception:
            meta = {}
        meta["task_id"] = data.get("task_id", task_id)
        meta["agent"] = data.get("agent", meta.get("agent", ""))
        meta["progress"] = dict(data.get("progress") or {})
        meta["state"] = data.get("state", "")
        meta["wait_human"] = bool(data.get("state") == PENDING_REVIEW)
        meta["updated_at"] = data.get("updated_at")
        store._write_json(path, meta)
        return data


def _record_rejection(store, task_id: str, current: str, to: str,
                      by: str, reason: str) -> None:
    """非法流转留痕（append-only JSONL；失败仅日志，不掩盖原异常）。"""
    path = rejection_path(store, task_id)
    row = {"from": current, "to": to, "by": str(by), "reason": str(reason),
           "at": time.time()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("state 拒绝留痕写入失败: project=%s task=%s", store.dir.name, task_id)
