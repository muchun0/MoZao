# -*- coding: utf-8 -*-
"""编排计划（REQ-R32 F1 / TECH-R32 §4）。

`projects/<id>/orchestration.json` 是 Supervisor 的唯一可变状态源：
阶段、章节游标、预算、决策环形日志、出错重试信息都在这里；
正文/大纲/质检等产物文件仍是各自权威源，计划只记录“进度与决策”。

不变式：
1. 同一项目同时只有一把编排锁（进程内 RLock），跨进程由生成槽位互斥兜底。
2. 写入一律原子写（ProjectStore._write_json）+ 版本号单调递增。
3. 有环状结构风险的内容不进计划（章节/大纲文本只记录摘要与路径）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

PLAN_FILE = "orchestration.json"
PLAN_VERSION = 1
DECISIONS_MAX = 200
STAGES_MAX_ATTEMPTS = 2

STAGE_STATUSES = ("pending", "ready", "running", "done", "confirmed", "skipped", "blocked", "failed")
CHAPTER_STEPS = ("plan_chapter", "write", "review", "ledger_commit", "next")

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def now() -> float:
    return time.time()


def lock_for(store) -> threading.RLock:
    pid = store.dir.name
    with _LOCKS_GUARD:
        lk = _LOCKS.get(pid)
        if lk is None:
            lk = threading.RLock()
            _LOCKS[pid] = lk
        return lk


def plan_path(store) -> Path:
    return store.dir / PLAN_FILE


def load_plan(store) -> dict | None:
    try:
        d = store._read_json(plan_path(store), None)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def save_plan(store, plan: dict) -> dict:
    plan["version"] = int(plan.get("version", 0)) + 1
    plan["updated_at"] = now()
    store._write_json(plan_path(store), plan)
    return plan


def delete_plan(store) -> None:
    try:
        plan_path(store).unlink()
    except FileNotFoundError:
        pass


# ---------- 从产物构建计划 ----------
def _outline_total(store, cfg) -> int:
    try:
        outline = store._read_json(store.outline_path(), None) or {}
        chs = outline.get("chapters") or []
        if chs:
            return len(chs)
    except Exception:
        pass
    return int(getattr(cfg, "num_chapters", 0) or 0)


def _chapter_exists(store, no: int) -> bool:
    return store.chapter_path(no).exists()


def _review_has(store, no: int) -> bool:
    try:
        recs = json.loads(store.review_path().read_text(encoding="utf-8"))
        return any(int(r.get("chapter", 0)) == no for r in recs)
    except Exception:
        return False


def _review_passed(store, no: int) -> tuple[bool, dict]:
    try:
        recs = json.loads(store.review_path().read_text(encoding="utf-8"))
    except Exception:
        return True, {}
    rec = next((r for r in recs if int(r.get("chapter", 0)) == no), None)
    if not rec:
        return True, {}
    qc = rec.get("quality_check") or {}
    return bool(qc.get("passed", True)), rec


def _max_steps(cfg, total: int) -> int:
    fixed = int(getattr(cfg, "supervisor_max_steps", 0) or 0)
    if fixed > 0:
        return min(fixed, 5000)
    return min(max(200, 10 + 5 * max(0, total)), 5000)


def _stage(key: str, role: str, agent: str) -> dict:
    return {
        "id": f"stage:{key}",
        "kind": "stage",
        "key": key,
        "role": role,
        "agent": agent,
        "status": "pending",
        "attempts": 0,
        "max_attempts": STAGES_MAX_ATTEMPTS,
        "digest": "",
        "task_id": "",
        "run_id": "",
        "error": "",
    }


def build_plan(store, cfg, mode: str = "stepwise", source: str = "new") -> dict:
    """按现有产物初始化计划（内部步骤模式）。

    阶段只是对外投影；R32 的章节循环按 1..N 顺序推进，不依赖阶段完成。
    """
    outline_done = bool(store._read_json(store.outline_path(), None))
    research_done = store.research_path().exists() or outline_done
    world_done = store.world_path().exists()
    total = _outline_total(store, cfg) or 1
    done_chs = [int(p.stem) for p in store.chapters_dir.glob("*.json") if p.stem.isdigit()]
    chapters_done = len(done_chs) >= total and total > 0

    stages = [
        _stage("research", "researcher", "researcher"),
        _stage("outline", "planner", "architect.outline"),
        _stage("world", "worldbuilder", "architect.world"),
        _stage("chapters", "writer", "writer.chapter"),
    ]
    stages[0].update(status="done" if research_done else "pending")
    stages[1].update(status="done" if outline_done else "pending")
    stages[2].update(status="done" if world_done else "pending")
    stages[3].update(status="done" if chapters_done else "pending")
    if not bool(getattr(cfg, "with_research", False)):
        stages[0].update(status="skipped", reason="with_research=false")

    current = next((n for n in range(1, total + 1) if not _chapter_exists(store, n)), total)
    step = "plan_chapter" if not _chapter_exists(store, current) else "next"
    plan = {
        "schema_version": PLAN_VERSION,
        "version": 0,
        "project_id": store.dir.name,
        "plan_id": uuid_like(),
        "mode": mode if mode in ("stepwise", "auto") else "stepwise",
        "source": source,
        "status": "done" if chapters_done else "running",
        "created_at": now(),
        "updated_at": now(),
        "stages": stages,
        "chapter_cycle": {
            "from": 1,
            "to": total,
            "current": current,
            "current_step": step,
            "steps": list(CHAPTER_STEPS),
        },
        "cursor": {"node_id": f"chapter:{current}", "chapter": current, "step": step},
        "budget": {
            "steps_used": 0,
            "max_steps": _max_steps(cfg, total),
            "repairs_used": 0,
            "max_repairs": max(0, int(getattr(cfg, "supervisor_max_repairs", 3) or 3)),
            "llm_calls": 0,
            "tokens": 0,
            "cost": 0.0,
            "started_at": now(),
        },
        "chapters": {},
        "decisions": [],
        "last_error": {},
    }
    sync_chapters(store, plan, cfg)
    return plan


def uuid_like() -> str:
    import uuid
    return uuid.uuid4().hex[:16]


def ensure_plan(store, cfg, mode: str = "stepwise", source: str = "new",
                rebuild: bool = False) -> dict:
    """读取或构建计划；模式变化重建（保留决策日志）。"""
    with lock_for(store):
        plan = None if rebuild else load_plan(store)
        if plan and plan.get("mode") in ("stepwise", "auto"):
            if mode in ("stepwise", "auto"):
                plan["mode"] = mode
            sync_chapters(store, plan, cfg)
            return plan
        old_decisions = (plan or {}).get("decisions") or []
        plan = build_plan(store, cfg, mode=mode, source=source)
        if old_decisions:
            plan["decisions"] = old_decisions[-DECISIONS_MAX:]
        save_plan(store, plan)
        return plan


def sync_chapters(store, plan: dict, cfg) -> dict:
    """把章节循环游标与前文产物对齐（幂等，不覆盖已有修复计数）。"""
    total = int((plan.get("chapter_cycle") or {}).get("to") or 1)
    chapters = plan.setdefault("chapters", {})
    qc_impl = bool(getattr(getattr(cfg, "agent_guard", None), "quality_check", True))
    first_incomplete = 0
    for no in range(1, total + 1):
        exists = _chapter_exists(store, no)
        rec = chapters.setdefault(str(no), {
            "status": "pending", "step": "plan_chapter",
            "attempts": 0, "repairs_used": 0, "last_issue": "",
        })
        if rec.get("human_accepted"):
            # 人工放行过（接受当前稿）：产物存在即视为完成，不再回落到修复
            rec["status"] = "done"
            rec["step"] = "done"
        elif not exists:
            rec["status"] = "pending"
            rec["step"] = "plan_chapter"
        elif qc_impl and not _review_has(store, no):
            rec["status"] = "running"
            rec["step"] = "review"
        elif qc_impl and not _review_passed(store, no)[0] and \
                rec.get("status") not in ("failed", "blocked"):
            # 已审但没过门：产物「存在」不等于「合格」，不能宣告 done，
            # 否则计划会在读路径上自动变成全书完成（修复还没收敛）。
            rec["status"] = "running"
            rec["step"] = "repair"
        elif rec.get("status") not in ("done", "failed", "blocked"):
            rec["status"] = "done"
            rec["step"] = "done"
        if rec.get("status") != "done" and first_incomplete == 0:
            first_incomplete = no

    if first_incomplete == 0:
        # 有阻塞工单/待人工时不能宣告完成：用户还没放行，读起来会是「全流程已完成」的假象
        try:
            from . import handover as _ho
            if _ho.pending_count(store, blocking_only=True) > 0:
                _stage_by_key(plan, "chapters")["status"] = "running"
                plan["chapter_cycle"]["current"] = total
                plan["chapter_cycle"]["current_step"] = "next"
                plan["cursor"] = {"node_id": f"chapter:{total}", "chapter": total, "step": "next"}
                return plan
        except Exception:
            pass
        _stage_by_key(plan, "chapters")["status"] = "done"
        plan["status"] = "done"
        plan["chapter_cycle"]["current"] = total
        plan["chapter_cycle"]["current_step"] = "next"
        plan["cursor"] = {"node_id": f"chapter:{total}", "chapter": total, "step": "next"}
        return plan

    cur = first_incomplete
    plan["chapter_cycle"]["current"] = cur
    rec = chapters.setdefault(str(cur), {"status": "pending", "step": "plan_chapter",
                                         "attempts": 0, "repairs_used": 0})
    plan["chapter_cycle"]["current_step"] = rec.get("step") or "plan_chapter"
    plan["cursor"] = {"node_id": f"chapter:{cur}", "chapter": cur,
                      "step": plan["chapter_cycle"]["current_step"]}
    if str(plan.get("status") or "") == "done":
        plan["status"] = "running"
    return plan


def stage_by_key(plan: dict, key: str) -> dict | None:
    return next((s for s in (plan.get("stages") or []) if s.get("key") == key), None)


_stage_by_key = stage_by_key


def stage_node_id(key: str) -> str:
    return f"stage:{key}"


def chapter_node_id(no: int) -> str:
    return f"chapter:{int(no)}"


def node_status(plan: dict, node_id: str) -> str:
    if node_id.startswith("stage:"):
        st = stage_by_key(plan, node_id.split(":", 1)[1])
        return str((st or {}).get("status") or "pending")
    no = node_id.split(":", 1)[1]
    return str(((plan.get("chapters") or {}).get(str(no)) or {}).get("status") or "pending")


def set_node(plan: dict, node_id: str, **kw) -> None:
    if node_id.startswith("stage:"):
        st = stage_by_key(plan, node_id.split(":", 1)[1])
        if st is not None:
            st.update(kw)
        return
    no = node_id.split(":", 1)[1]
    rec = (plan.setdefault("chapters", {})).setdefault(str(no), {
        "status": "pending", "step": "plan_chapter", "attempts": 0, "repairs_used": 0,
    })
    rec.update(kw)


def add_decision(plan: dict, node_id: str, kind: str, by: str, reason_code: str,
                 detail: str = "", **extra) -> dict:
    item = {
        "at": now(), "node": node_id, "kind": kind, "by": by,
        "reason_code": reason_code, "detail": detail, **extra,
    }
    ring = plan.setdefault("decisions", [])
    ring.append(item)
    if len(ring) > DECISIONS_MAX:
        del ring[: len(ring) - DECISIONS_MAX]
    return item


def update_budget(plan: dict, steps: int = 0, repairs: int = 0,
                  llm_calls: int | None = None, tokens: int | None = None) -> dict:
    b = plan.setdefault("budget", {})
    b["steps_used"] = int(b.get("steps_used", 0)) + max(0, steps)
    b["repairs_used"] = int(b.get("repairs_used", 0)) + max(0, repairs)
    if llm_calls is not None:
        b["llm_calls"] = int(b.get("llm_calls", 0)) + max(0, int(llm_calls))
    if tokens is not None:
        b["tokens"] = int(b.get("tokens", 0)) + max(0, int(tokens))
    return b


def budget_exhausted(plan: dict) -> tuple[bool, str]:
    b = plan.get("budget") or {}
    if int(b.get("max_steps", 0) or 0) and int(b.get("steps_used", 0)) >= int(b["max_steps"]):
        return True, "max_steps"
    if int(b.get("max_repairs", 0) or 0) and int(b.get("repairs_used", 0)) >= int(b["max_repairs"]):
        return True, "max_repairs"
    return False, ""


def reconcile(store, plan: dict, cfg) -> dict:
    """只读对账：产物缺失/游标倒退只记录，不静默推进。"""
    issues: list[dict] = []
    for st in plan.get("stages") or []:
        key = st.get("key")
        if st.get("status") in ("done", "confirmed"):
            exists = {
                "research": store.research_path().exists(),
                "outline": store.outline_path().exists(),
                "world": store.world_path().exists(),
            }.get(key)
            if key in ("research", "outline", "world") and not exists:
                st["status"] = "pending"
                st["error"] = "artifact_missing"
                issues.append({"node": f"stage:{key}", "issue": "artifact_missing"})
    for no in range(1, int((plan.get("chapter_cycle") or {}).get("to") or 0) + 1):
        has = _chapter_exists(store, no)
        rec = (plan.get("chapters") or {}).get(str(no)) or {}
        if rec.get("status") == "done" and not has:
            rec["status"] = "pending"
            rec["step"] = "plan_chapter"
            issues.append({"node": f"chapter:{no}", "issue": "chapter_missing"})
    sync_chapters(store, plan, cfg)
    return {"issues": issues}


def advance_chapter(plan: dict, total: int) -> None:
    """当前章完成 → 游标 +1；全书完成 → 计划 done。"""
    cc = plan.setdefault("chapter_cycle", {})
    cur = int(cc.get("current") or 1)
    rec = (plan.get("chapters") or {}).get(str(cur)) or {}
    rec.update(status="done", step="done")
    nxt = cur + 1
    if nxt > total:
        _stage_by_key(plan, "chapters")["status"] = "done"
        plan["status"] = "done"
        cc.update(current=total, current_step="next")
        plan["cursor"] = {"node_id": f"chapter:{total}", "chapter": total, "step": "next"}
        return
    cc.update(current=nxt, current_step="plan_chapter")
    plan["cursor"] = {"node_id": f"chapter:{nxt}", "chapter": nxt, "step": "plan_chapter"}
    plan.setdefault("chapters", {}).setdefault(str(nxt), {
        "status": "pending", "step": "plan_chapter", "attempts": 0, "repairs_used": 0,
    }).update(status="pending", step="plan_chapter")


def stage_projection(plan: dict) -> list[dict]:
    """给旧前端流程条用的阶段投影（字段与 R27 stages 同构）。"""
    done_ch = int((plan.get("chapter_cycle") or {}).get("current", 1)) - 1
    total = int((plan.get("chapter_cycle") or {}).get("to") or 1)
    out = []
    for st in plan.get("stages") or []:
        status = st.get("status") or "pending"
        item = {"key": st.get("key"), "status": status, "digest": st.get("digest", ""),
                "error": st.get("error", ""), "role": st.get("role", "")}
        if st.get("key") == "chapters":
            item["progress"] = {"done": max(0, done_ch), "total": total}
            if status == "done":
                item["progress"] = {"done": total, "total": total}
        out.append(item)
    return out


def chapter_number_from_node(node_id: str) -> int:
    m = re.match(r"^chapter:(\d+)$", str(node_id or ""))
    return int(m.group(1)) if m else 0


ROLE_INVOCATIONS_MAX = 500


def artifact_digest(obj: Any) -> str:
    """产物摘要（sha1），用于 plans.digest / insights 的稳定校验。"""
    import hashlib
    raw = json.dumps(obj if obj is not None else {}, ensure_ascii=False, sort_keys=True,
                     default=str).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def role_invocations_path(store) -> Path:
    return store.dir / "role_invocations.json"


def record_role_invocation(store, node_id: str, role: str, status: str,
                           digest: str = "", task_id: str = "", words: int = 0) -> dict:
    """角色调用台账（REQ-R32 §8.3）：只追加、有上限、原子写；失败降级由调用方兜底。"""
    with lock_for(store):
        try:
            rows = json.loads(role_invocations_path(store).read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                rows = []
        except Exception:
            rows = []
        try:
            run_id = str((store.current_run() or {}).get("run_id", ""))
        except Exception:
            run_id = ""
        row = {"at": now(), "node": node_id, "role": role, "status": status,
               "digest": digest, "task_id": task_id, "run_id": run_id,
               "words": int(words or 0)}
        rows.append(row)
        if len(rows) > ROLE_INVOCATIONS_MAX:
            rows = rows[-ROLE_INVOCATIONS_MAX:]
        store._write_json(role_invocations_path(store), rows)
        return row


def load_role_invocations(store) -> list:
    try:
        rows = json.loads(role_invocations_path(store).read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except Exception:
        return []
