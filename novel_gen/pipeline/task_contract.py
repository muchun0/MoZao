# -*- coding: utf-8 -*-
"""任务契约（REQ-R30 M0）：把一次 Agent run 的「身份 + 范围 + 基线 + 策略」固化。

设计约束（TECH-R30-M0M1 §3.1）：
- 契约是只读事实，不参与判定，不做业务分支；
- 基线记录 world/outline 的 selected 版本与已写最大章，供快照与回放对齐；
- 失败全部降级为 None/0，不允许因为读版本索引失败而中断生成。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


# Agent 产品 → 任务类型（未知 Agent 不建契约，调用方按开关回退）
KIND_BY_AGENT: dict[str, str] = {
    "researcher": "research",
    "architect.outline": "outline_gen",
    "architect.world": "world_gen",
    "architect.volume": "volume_gen",
    "writer.chapter": "chapter_write",
}

# 交接策略默认值（与 REQ-R30 §5 对齐；实际值由配置覆盖）
DEFAULT_TRIGGERS: tuple[str, ...] = ("rule_hit", "low_confidence", "proposal", "budget", "human_stop")


def kind_of(agent: str) -> str:
    """Agent 产品 → 任务类型；未注册返回空串（调用方据此跳过契约）。"""
    return KIND_BY_AGENT.get(agent, "")


def _selected_version(store, kind: str) -> int | None:
    """读版本索引中 selected 版本号；索引缺失/损坏返回 None（降级）。"""
    try:
        path = Path(store.dir) / kind / "versions.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        rec = (data or {}).get("all") or {}
        selected = rec.get("selected")
        return int(selected) if selected is not None else None
    except Exception:
        return None


def _max_chapter_no(store) -> int:
    """已写最大章号（书级事实基线）；读失败返回 0。"""
    try:
        return max((int(c.no) for c in store.list_chapters()), default=0)
    except Exception:
        return 0


def normalize_scope(agent: str, params: dict | None) -> dict:
    """把请求参数归一成契约里的 scope（只做记录，不参与章号解析）。"""
    params = params or {}
    if agent != "writer.chapter":
        return {"mode": "all"}
    explicit = params.get("_chapters")
    if isinstance(explicit, (list, tuple)) and explicit:
        nums: list[int] = []
        for x in explicit:
            try:
                nums.append(int(x))
            except (TypeError, ValueError):
                continue
        if nums:
            return {"mode": "explicit", "ch": nums}
    scope = str(params.get("scope") or "all")
    if scope == "single":
        try:
            return {"mode": "single", "ch": [int(params.get("ch_no") or 0)]}
        except (TypeError, ValueError):
            return {"mode": "single", "ch": []}
    if scope == "range":
        try:
            return {"mode": "range", "from": int(params.get("from_ch") or 1),
                    "to": int(params.get("to_ch") or 0)}
        except (TypeError, ValueError):
            return {"mode": "range", "from": 1, "to": 0}
    return {"mode": "all"}


def build_manifest(store, task_id: str, agent: str, params: dict | None, *,
                   operator: str = "", user_prompt: str = "",
                   skill_ids: tuple | list | None = None) -> dict:
    """构造任务契约 dict（可直接作为 state.json 的 manifest 部分落盘）。"""
    cfg = store.load_config()
    params = dict(params or {})
    policy = {
        "triggers": list(DEFAULT_TRIGGERS),
        "sample_rate": int(getattr(cfg, "handover_sample_rate", 20) or 0),
        "low_confidence_count": int(getattr(cfg, "handover_low_confidence_count", 3) or 0),
        "flags": {
            "task_state_machine": bool(getattr(cfg, "task_state_machine", False)),
            "handover_v1": bool(getattr(cfg, "handover_v1", False)),
            "ledger_proposal": bool(getattr(cfg, "ledger_proposal", False)),
        },
    }
    return {
        "task_id": str(task_id),
        "project_id": str(Path(store.dir).name),
        "agent": str(agent),
        "kind": kind_of(agent) or str(agent),
        "scope": normalize_scope(agent, params),
        "baseline": {
            "world_v": _selected_version(store, "world"),
            "outline_v": _selected_version(store, "outline"),
            "book_facts_at_ch": _max_chapter_no(store),
        },
        "handover_policy": policy,
        "operator": str(operator or ""),
        "params": params,
        "user_prompt": str(user_prompt or ""),
        "skill_ids": [int(x) for x in (skill_ids or ())],
        "model": str(getattr(cfg.llm, "model", "") or ""),
        "created_at": time.time(),
    }
