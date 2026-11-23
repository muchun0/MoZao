# -*- coding: utf-8 -*-
"""任务快照（REQ-R30 M0 / P0-2）：逐步记录输入引用、产出引用、模型与关键开关。

约束：
- append-only 语义（重写整个文件但只追加一步），原子写；
- 步数上限 500，超出丢弃最旧（保留 run_start 与末 499 步）；
- 记录失败只降级留痕，绝不阻断生成（非核心路径降级必须 logger）。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_STEPS = 500

# 只记录与复现/回滚相关的开关，避免把上百个 flag 全塞进每一步
SWITCH_KEYS: tuple[str, ...] = (
    "task_state_machine", "handover_v1", "ledger_proposal",
    "chapter_commit", "chapter_design", "scene_split", "plan_graph",
    "book_facts", "reader_ledger", "state_graph", "versioned_product",
    "auto_rewrite_rounds", "verify_every", "stage_gate",
)


def snapshot_path(store, task_id: str) -> Path:
    return Path(store.dir) / "runs" / str(task_id) / "snapshot.json"


def _load(store, task_id: str) -> dict:
    path = snapshot_path(store, task_id)
    if not path.exists():
        return {"task_id": str(task_id), "next_seq": 1, "steps": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("steps"), list):
            data.setdefault("task_id", str(task_id))
            data.setdefault("next_seq", len(data["steps"]) + 1)
            return data
    except Exception:
        logger.warning("snapshot.json 损坏，按空快照重建: project=%s task=%s",
                       store.dir.name, task_id, exc_info=True)
    return {"task_id": str(task_id), "next_seq": 1, "steps": []}


def switches_of(cfg) -> dict:
    """取关键开关快照（读属性失败跳过该项，不影响记录）。"""
    out: dict = {}
    for key in SWITCH_KEYS:
        try:
            out[key] = getattr(cfg, key)
        except Exception:
            continue
    return out


def record_step(store, task_id: str, node: str, *, in_refs: list | None = None,
                out_refs: list | None = None, model: str = "",
                switches: dict | None = None, elapsed_ms: int = 0,
                degraded: bool = False, extra: dict | None = None) -> str:
    """追加一步，返回 `runs/<task_id>/snapshot.json#step-N` 引用。"""
    if not task_id:
        return ""
    path = snapshot_path(store, task_id)
    try:
        data = _load(store, task_id)
        seq = int(data.get("next_seq") or 1)
        step = {
            "step_id": f"step-{seq}",
            "node": str(node),
            "in_refs": list(in_refs or []),
            "out_refs": list(out_refs or []),
            "model": str(model or ""),
            "switches": dict(switches or {}),
            "elapsed_ms": int(elapsed_ms or 0),
            "degraded": bool(degraded),
            "at": time.time(),
        }
        if extra:
            step["extra"] = extra
        steps = data.get("steps") or []
        steps.append(step)
        if len(steps) > MAX_STEPS:
            # 保留第一步（run_start），其余丢最旧
            head = steps[:1]
            tail = steps[-(MAX_STEPS - 1):]
            steps = head + tail
        data["steps"] = steps
        data["next_seq"] = seq + 1
        data["updated_at"] = time.time()
        store._write_json(path, data)
        return f"runs/{task_id}/snapshot.json#step-{seq}"
    except Exception:
        logger.exception("snapshot 记录失败（降级）: project=%s task=%s node=%s",
                         store.dir.name, task_id, node)
        return ""


def read_snapshot(store, task_id: str) -> dict:
    """只读快照（供 API/测试使用；缺失返回空结构）。"""
    return _load(store, task_id)


def latest_step(store, task_id: str) -> dict | None:
    steps = (_load(store, task_id).get("steps") or [])
    return steps[-1] if steps else None
