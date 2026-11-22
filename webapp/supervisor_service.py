# -*- coding: utf-8 -*-
"""固化 Supervisor 的 Web 接线（REQ-R32 F4/F6 / TECH-R32 §9）。

职责边界：
- 读配置/产物、推进 `orchestration.json`、投影旧 `generation_stage.json`（唯一写者）；
- 按决策动作经 `generate_service.start_agent_run(internal=...)` 派发内部 run；
- 失败/预算/修复耗尽时落 handover 工单并停任务；
- 提供 `GET /supervisor` 只读观测与 shadow 决策日志。

不做：LLM 调用、账本写入、正文生成（全部复用既有内核）。
"""
from __future__ import annotations

import copy
import json
import logging
import uuid
from pathlib import Path

from fastapi import HTTPException

from novel_gen.pipeline import orchestration as orch
from novel_gen.pipeline import supervisor as sup
from novel_gen.pipeline import handover, roles

logger = logging.getLogger(__name__)

MODES = ("off", "shadow", "on")
_DEP_KEYS = {
    "task_state_machine": "任务状态机（M0）",
    "handover_v1": "人工交接工单（M1）",
}


def mode_of(cfg) -> str:
    mode = str(getattr(cfg, "supervisor_mode", "off") or "off").strip().lower()
    if mode and mode not in MODES:
        logger.warning("supervisor_mode 非法值 %r，按 off 处理", mode)
        return "off"
    return mode or "off"


def load_cfg(store):
    """Supervisor 的模式以项目快照为准（灰度按书开关，见 TECH §10.0）。

    后台全局开关表里 `supervisor_mode` 也有默认行，若在此叠加全局覆盖，
    任何书都无法单独设 shadow/on，因此这里刻意不吃全局覆盖。
    """
    return store.load_config()


def mode_for(store) -> str:
    """给 generation_flow 的三态分流用：与 Supervisor 内部读同一份项目配置。"""
    return mode_of(load_cfg(store))


def check_dependencies(cfg) -> None:
    """on 的依赖矩阵（TECH §5.1）：缺任一 → 400，不带病启动。"""
    missing = []
    for key, label in _DEP_KEYS.items():
        if not bool(getattr(cfg, key, False)):
            missing.append(label)
    guard = getattr(cfg, "agent_guard", None)
    if guard is None or not bool(getattr(guard, "quality_check", True)):
        missing.append("章级质检（agent_guard.quality_check）")
    if int(getattr(cfg, "auto_rewrite_rounds", 0) or 0) != 0:
        missing.append("auto_rewrite_rounds=0（Supervisor 独占修复预算）")
    if missing:
        raise HTTPException(status_code=400,
                            detail="Supervisor 依赖未满足：" + "；".join(missing))


def check_shadow_dependencies(cfg) -> None:
    """shadow 只依赖任务状态机（TECH §10.1）：读不到章状态就无法比对决策。"""
    if not bool(getattr(cfg, "task_state_machine", False)):
        raise HTTPException(status_code=400,
                            detail="Supervisor 影子模式依赖未满足：任务状态机（M0）")


def _mode_or_400(store, cfg=None) -> str:
    cfg = cfg or load_cfg(store)
    mode = mode_of(cfg)
    if mode == "on":
        check_dependencies(cfg)
    elif mode == "shadow":
        check_shadow_dependencies(cfg)
    return mode


def _total(plan: dict) -> int:
    return int((plan.get("chapter_cycle") or {}).get("to") or 0)


def _review_of(store, no: int) -> dict | None:
    return next((r for r in store.load_review() if int(r.get("chapter", 0)) == int(no)), None)


def _active_run(store) -> bool:
    """是否有 run 真正在执行。

    `task.json` 在 pending_review/已收尾的 task 上也会保留（供进度展示），
    所以不能只看文件是否存在——否则放行工单后永远被判「active_run」而停住。
    活线程优先；没有活线程时按 manifest 的 state 判定（只有 running 才算在跑）。
    """
    try:
        from . import core
        handle = core._RUNS.get(store.dir.name)
        if handle is not None and handle.thread is not None and handle.thread.is_alive():
            return True
    except Exception:
        pass
    try:
        meta = json.loads((Path(store.dir) / "task.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    state = str(meta.get("state") or "").strip().lower()
    return state == "running" and not bool(meta.get("wait_human"))


def _blocking_handover(store, task_id: str = "") -> dict | None:
    rows = handover.list_handovers(store, status=handover.OPEN, limit=200)
    blocked = [h for h in rows if h.get("blocking")]
    if not blocked:
        return None
    if task_id:
        for h in blocked:
            if str(h.get("task_id") or "") == task_id:
                return h
    return blocked[0]


def _reconcile_plan_status(store, plan: dict) -> bool:
    """读路径对账：有阻塞工单且没有活线程时，计划不可能真在跑。

    历史缺陷：转人工（fail）路径没落盘，计划文件停在 running，UI 顶栏显示
    「运行中」而流程条显示「已暂停」，读起来自相矛盾。这里按真实事实收敛。
    """
    try:
        if plan.get("status") != "running" or _active_run(store):
            return False
        if handover.pending_count(store, blocking_only=True) <= 0:
            return False
        _set_plan_status(plan, "blocked", "pending_handover")
        orch.add_decision(plan, str((plan.get("cursor") or {}).get("node_id") or ""),
                          "stop", "system", "pending_handover", "有阻塞工单待人工处理")
        orch.save_plan(store, plan)
        return True
    except Exception:
        logger.exception("计划状态对账失败（降级）: project=%s", store.dir.name)
        return False


def _task_id(store) -> str:
    try:
        meta = json.loads((Path(store.dir) / "task.json").read_text(encoding="utf-8"))
        return str(meta.get("task_id") or "")
    except Exception:
        return ""


def _pending_review(store, task_id: str) -> bool:
    try:
        from novel_gen.pipeline import task_state
        state = task_state.load_state(store, task_id) if task_id else None
        return bool(state and state.get("state") == task_state.PENDING_REVIEW)
    except Exception:
        return False


def build_ctx(store, plan: dict, cfg) -> dict:
    cc = plan.get("chapter_cycle") or {}
    no = int(cc.get("current") or 1)
    return {
        "cfg": cfg,
        "store": store,
        "chapter_exists": store.chapter_path(no).exists() if no else False,
        "review_exists": _review_of(store, no) is not None,
        "review_passed": bool(((_review_of(store, no) or {}).get("quality_check") or {}).get("passed", True)),
        "active_run": _active_run(store),
        "blocking_handover": _blocking_handover(store, _task_id(store)),
        "pending_review": _pending_review(store, _task_id(store)),
        "stopped": plan.get("status") == "stopped",
    }


def _set_plan_status(plan: dict, status: str, reason: str = "") -> None:
    plan["status"] = status
    if reason:
        plan["last_error"] = {"code": reason, "at": orch.now()}


def _block(store, plan: dict, action: sup.Action) -> None:
    orch.add_decision(plan, action.node_id, "stop", "supervisor", action.reason_code,
                      action.detail)
    _set_plan_status(plan, "blocked", action.reason_code)


def _fail_to_handover(store, plan: dict, action: sup.Action, cfg) -> None:
    """修复/重试耗尽 → 落 blocking 工单，任务停 pending_review。"""
    no = orch.chapter_number_from_node(action.node_id)
    task_id = _task_id(store) or plan.get("task_id") or f"r32_{plan.get('plan_id', '')}"
    trigger = "repair_exhausted" if action.reason_code.startswith("repair_exhausted") \
        else "rule_hit"
    try:
        if trigger == "repair_exhausted":
            h = handover.create_repair_exhausted_handover(store, task_id, no, action.detail)
        else:
            h = handover.create_handover(
                store, task_id=task_id, trigger="rule_hit", severity="high", blocking=True,
                evidence={"issue_ids": [], "rule": action.reason_code,
                          "quote": {"ch": no, "offset": 0, "snippet": action.detail or action.reason_code}},
                dedupe_key=f"r32:{task_id}:{action.node_id}:{action.reason_code}")
        if isinstance(h, tuple):
            h = h[0]
        logger.info("Supervisor 转人工: project=%s node=%s trigger=%s handover=%s",
                    store.dir.name, action.node_id, trigger, (h or {}).get("handover_id"))
    except Exception:
        logger.exception("交接工单创建失败（降级为失败状态）: project=%s", store.dir.name)
    try:
        from novel_gen.pipeline import task_state
        state = task_state.load_state(store, task_id)
        if state and state.get("state") not in (task_state.PENDING_REVIEW, task_state.DONE):
            task_state.mark_pending(store, task_id, handover_ids=[], remaining_chapters=[],
                                    by="system", reason=action.reason_code)
            task_state.project_now(store, task_id)
    except Exception:
        logger.exception("任务转待审失败（降级）: project=%s", store.dir.name)
    _set_plan_status(plan, "blocked", action.reason_code)


# ---------- 动作执行 ----------
def _dispatch(store, plan: dict, action: sup.Action, user: dict | None, cfg) -> dict:
    """执行决策动作；返回给前端的摘要。仅 run/repair 真正起线程。"""
    if action.kind in ("wait", "blocked", "confirm", "done"):
        if action.kind == "done":
            _set_plan_status(plan, "done")
        elif action.kind == "blocked":
            _set_plan_status(plan, "blocked", action.reason_code)
        orch.add_decision(plan, action.node_id, "decision", "supervisor",
                          action.reason_code, action.detail)
        # 决策结果（done/blocked）必须落盘，否则文件里会一直停在「运行中」
        orch.save_plan(store, plan)
        return action.to_dict()
    if action.kind == "fail":
        _fail_to_handover(store, plan, action, cfg)
        # 失败/转人工同样要落盘：否则计划文件停在上一轮的 running，刷新后
        # 既看不到「已转人工」，续跑时还会被当成可继续自动派发。
        orch.save_plan(store, plan)
        return action.to_dict()

    from . import generate_service
    # 每个节点一个独立 task：R30 的 task_state 是 run 级状态机，
    # 复用同一个 task_id 会撞上 DONE→RUNNING 非法流转（且 manifest 会停留在首个节点）。
    task_id = f"r32_{uuid.uuid4().hex[:12]}"
    plan["task_id"] = task_id
    no = orch.chapter_number_from_node(action.node_id)
    action_name = action.action or ""
    if action.kind == "repair":
        action_name = "repair"
    params: dict = {"scope": "single", "ch_no": no} if no else {}
    if action.kind == "run" and action.node_id.startswith("stage:"):
        key = action.node_id.split(":", 1)[1]
        if key == "chapters":
            action = sup.chapter_action(plan, build_ctx(store, plan, cfg), _total(plan))
            return _dispatch(store, plan, action, user, cfg)
        params = {}
        action_name = ""
    if action_name == "repair":
        review = _review_of(store, no) or {}
        params["_repair_focus"] = sup.repair_scope_text(review)
    req = generate_service.AgentRunReq(
        agent=action.agent, params=params,
        user_prompt="", skill_ids=(), allow_inconsistent=False)
    if action.node_id.startswith("chapter:"):
        # 章节循环一旦开跑就把 chapters 阶段置 running（幂等）：否则计划里长期留 pending，
        # 放行工单/切模式时又会走「chapter_loop_start」，把已完成的章重写一遍。
        ch_st = orch.stage_by_key(plan, "chapters") or {}
        if ch_st.get("status") in ("pending", "ready"):
            ch_st["status"] = "running"
        cc = plan.setdefault("chapter_cycle", {})
        if no:
            cc["current"] = int(no)
        cc["current_step"] = "repair" if action_name == "repair" else "write"
        orch.set_node(plan, action.node_id, status="running",
                      step="repair" if action_name == "repair" else "write",
                      task_id=task_id)
        plan["cursor"] = {"node_id": action.node_id, "chapter": no,
                          "step": "repair" if action_name == "repair" else "write"}
    else:
        orch.set_node(plan, action.node_id, status="running", task_id=task_id)
    if action_name == "repair" and no:
        # 修复必须计数（章节 + 任务预算），否则「修不完」会无限重派，每轮都真实烧钱
        sup.mark_repair_used(plan, no)
        orch.update_budget(plan, steps=1)
    else:
        orch.update_budget(plan, steps=1)
    orch.add_decision(plan, action.node_id, "dispatch", "supervisor",
                      action.reason_code, action.detail,
                      role=action.role, agent=action.agent, action=action_name)
    orch.save_plan(store, plan)
    try:
        _auto_gate(store, action.node_id, user)
        body = generate_service.start_agent_run(
            store, req, user or {},
            internal={"node": action.node_id, "role": action.role,
                      "attempt": int((plan.get("chapters", {}).get(str(no)) or {}).get("attempts", 0)),
                      "action": action_name, "task_id": task_id})
    except Exception:
        orch.set_node(plan, action.node_id, status="pending", error="start_failed")
        orch.save_plan(store, plan)
        raise
    if action.node_id.startswith("chapter:"):
        orch.set_node(plan, action.node_id, run_id=str(body.get("run_id") or ""))
        orch.save_plan(store, plan)
    else:
        orch.set_node(plan, action.node_id, run_id=str(body.get("run_id") or ""))
        orch.save_plan(store, plan)
    return {**action.to_dict(), "run_id": body.get("run_id"), "task_id": body.get("task_id"),
            "started": True}


def _auto_gate(store, node_id: str, user: dict | None) -> None:
    """Supervisor 推进世界观前放行大纲门（auto 模式无人工确认卡）。"""
    if node_id != "stage:world":
        return
    try:
        from .generation_flow import _approve_gate
        _approve_gate(store, "outline", user or {})
    except HTTPException:
        raise
    except Exception:
        logger.exception("大纲门放行失败（继续派发）: project=%s", store.dir.name)


def _shadow_log(store, plan: dict, action: sup.Action) -> None:
    try:
        from novel_gen.config import feature_default
        if not bool(feature_default("supervisor_shadow_log")):
            return
        path = Path(store.dir) / "shadow_decisions.jsonl"
        row = {"at": orch.now(), "plan_id": plan.get("plan_id"), "action": action.to_dict(),
               "cursor": plan.get("cursor"), "status": plan.get("status")}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > 1000:
            path.write_text("\n".join(lines[-1000:]) + "\n", encoding="utf-8")
    except Exception:
        logger.exception("shadow 决策日志写入失败（降级）: project=%s", store.dir.name)


# ---------- 对外入口 ----------
def start(store, user: dict, mode: str = "stepwise", source: str = "new") -> dict:
    cfg = load_cfg(store)
    smode = _mode_or_400(store, cfg)
    if smode == "off":
        raise HTTPException(status_code=404, detail="Supervisor 未启用")
    if smode == "shadow":
        return {"supported": True, "mode": "shadow", "started": False,
                "detail": "shadow 模式只计算不执行"}
    with orch.lock_for(store):
        plan = orch.ensure_plan(store, cfg, mode=mode, source=source, rebuild=False)
        if plan.get("status") in ("done", "aborted"):
            plan = orch.ensure_plan(store, cfg, mode=mode, source=source, rebuild=True)
        plan["status"] = "running"
        orch.save_plan(store, plan)
        action = sup.next_action(plan, build_ctx(store, plan, cfg))
        result = _dispatch(store, plan, action, user, cfg)
        _project(store, plan, cfg)
        return {"supported": True, "mode": smode, "plan_id": plan.get("plan_id"),
                "next_action": result, "plan": public_plan(plan)}


def set_mode(store, user: dict | None, mode: str) -> dict:
    """切换 auto/stepwise：写计划模式；auto 立即推进（分步下产物完成点才暂停）。"""
    cfg = load_cfg(store)
    smode = _mode_or_400(store, cfg)
    if smode != "on":
        return {"supported": True, "mode": smode, "detail": "shadow/off 不执行调度"}
    with orch.lock_for(store):
        plan = orch.ensure_plan(store, cfg, mode=mode, source="set_mode")
        plan["mode"] = mode
        if mode == "stepwise" and plan.get("status") == "done":
            plan["status"] = "running"
        orch.save_plan(store, plan)
        action = sup.next_action(plan, build_ctx(store, plan, cfg))
        result = _dispatch(store, plan, action, user, cfg) if action.kind in ("run", "repair", "fail") \
            else action.to_dict()
        _project(store, plan, cfg)
        return {"supported": True, "mode": mode, "plan_id": plan.get("plan_id"),
                "next_action": result, "plan": public_plan(plan)}


def confirm(store, user: dict, stage: str, action: str, digest: str = "",
            idempotency_key: str = "") -> dict:
    cfg = load_cfg(store)
    smode = _mode_or_400(store, cfg)
    if smode != "on":
        raise HTTPException(status_code=404, detail="Supervisor 未启用")
    if stage not in ("research", "outline", "world", "chapters"):
        raise HTTPException(status_code=400, detail=f"非法阶段：{stage}")
    with orch.lock_for(store):
        plan = orch.ensure_plan(store, cfg, mode="stepwise")
        st = orch.stage_by_key(plan, stage) or {}
        action = str(action or "").lower()
        if digest and st.get("digest") and digest != st.get("digest"):
            raise HTTPException(status_code=409, detail="产物已变更，请刷新后再确认")
        if action == "reject":
            _set_plan_status(plan, "stopped", "rejected")
            orch.add_decision(plan, f"stage:{stage}", "stop", "user", "rejected")
            orch.save_plan(store, plan)
            _project(store, plan, cfg)
            return {"stage": stage, "action": action, "stage_status": "stopped"}
        if action == "skip":
            st["status"] = "skipped"
            st["reason"] = "user_skip"
            orch.save_plan(store, plan)
            _project(store, plan, cfg)
            return {"stage": stage, "action": action, "stage_status": "skipped"}
        if action == "back_outline":
            for key in ("outline", "world", "chapters"):
                s = orch.stage_by_key(plan, key)
                if s:
                    s.update(status="pending", error="")
            plan["status"] = "running"
            orch.save_plan(store, plan)
            _project(store, plan, cfg)
            return {"stage": "outline", "action": action, "stage_status": "pending"}
        if action == "regenerate":
            orch.set_node(plan, f"stage:{stage}", status="pending", error="")
            plan["status"] = "running"
            orch.save_plan(store, plan)
            act = sup._stage_action(plan, orch.stage_by_key(plan, stage) or {}, "auto")
            result = _dispatch(store, plan, act, user, cfg)
            _project(store, plan, cfg)
            return {"stage": stage, "action": action, "result": result}
        if action != "continue":
            raise HTTPException(status_code=400, detail=f"非法确认动作：{action}")

        # 分步确认即「用户看过」：放行大纲/世界观审阅门，否则内部派发会被门禁 409 拦住
        if stage in ("outline", "world"):
            from .generation_flow import _approve_gate
            _approve_gate(store, stage, user or {})

        orch.set_node(plan, f"stage:{stage}", status="done", confirmed_at=orch.now(),
                      confirmed_by="user")
        if orch.stage_by_key(plan, "chapters") is st:
            plan["status"] = "running"
        orch.add_decision(plan, f"stage:{stage}", "confirm", "user", "user_confirmed")
        orch.save_plan(store, plan)
        act = sup.next_action(plan, build_ctx(store, plan, cfg))
        if act.kind == "confirm":
            # 用户确认后进入下一阶段：stepwise 下下一阶段仍需确认，但先置 ready
            key = act.stage_key or (act.node_id.split(":", 1)[1] if ":" in act.node_id else "")
            st2 = orch.stage_by_key(plan, key) or {}
            st2["status"] = "ready"
            orch.save_plan(store, plan)
            _project(store, plan, cfg)
            return {"stage": stage, "action": action, "stage_status": "done",
                    "next": {"kind": "confirm", "stage": key}}
        result = _dispatch(store, plan, act, user, cfg) if act.kind in ("run", "repair", "fail") \
            else act.to_dict()
        _project(store, plan, cfg)
        return {"stage": stage, "action": action, "stage_status": "done",
                "next": result}


def on_run_finished(store, agent: str, run_id: str, status: str,
                    node_id: str = "", role: str = "") -> dict:
    cfg = load_cfg(store)
    smode = mode_of(cfg)
    if smode == "off":
        return {"handled": False}
    plan = orch.load_plan(store)
    if plan is None:
        return {"handled": False, "reason": "no_plan"}
    if not node_id:
        if agent == "writer.chapter":
            # 页面直跑（无内部 node）时按产物定位章号：直接沿用游标会把这次 run
            # 记到别的章上（历史上出现过「写完第3章记成第2章」）。
            try:
                chs = store.list_chapters()
                if chs:
                    node_id = f"chapter:{max(int(c.no) for c in chs)}"
            except Exception:
                logger.exception("直跑章节定位失败（回退游标）: project=%s", store.dir.name)
        if not node_id:
            node_id = str(plan.get("cursor", {}).get("node_id") or "")
    if not role:
        role = _role_for_node(node_id)
    action = None
    missing: list[str] = []
    review = None
    try:
        missing = roles.outcome_check(store, role, node_id) if role else []
    except Exception:
        logger.exception("产物校验失败（按通过处理）: project=%s node=%s",
                         store.dir.name, node_id)
    if node_id.startswith("chapter:"):
        no = orch.chapter_number_from_node(node_id)
        review = _review_of(store, no)
    if smode == "shadow":
        shadow_plan = copy.deepcopy(plan)
        action = sup.decide_after_run(shadow_plan, node_id, role, status, cfg, review,
                                      missing, mode=plan.get("mode", "stepwise"))
        _shadow_log(store, shadow_plan, action)
        return {"handled": True, "mode": "shadow", "action": action.to_dict()}
    with orch.lock_for(store):
        plan = orch.load_plan(store) or plan
        action = sup.decide_after_run(plan, node_id, role, status, cfg, review,
                                      missing, mode=plan.get("mode", "stepwise"))
        orch.add_decision(plan, node_id, "run_finished", "supervisor",
                          str(action.reason_code or status),
                          f"role={role} status={status} action={action.kind}",
                          role=role, run_id=run_id)
        orch.save_plan(store, plan)
        result = _dispatch(store, plan, action, store_user(store), cfg)
        _project(store, plan, cfg)
        return {"handled": True, "mode": smode, "action": result,
                "plan": public_plan(plan)}


def store_user(store) -> dict:
    """内部派发用最小用户上下文（仅留痕 operator 名，不落 open_id）。"""
    try:
        from . import db
        uid = db.project_owner(store.dir.name)
        u = db.get_user(uid) if uid else None
        return {"username": (u or {}).get("username", ""), "id": uid or ""}
    except Exception:
        return {}


def _role_for_node(node_id: str) -> str:
    if node_id.startswith("stage:"):
        key = node_id.split(":", 1)[1]
        return {"research": "researcher", "outline": "planner",
                "world": "worldbuilder", "chapters": "writer"}.get(key, "")
    return "writer"


def public_plan(plan: dict) -> dict:
    stages = []
    for st in plan.get("stages") or []:
        stages.append({k: st.get(k) for k in
                       ("key", "role", "agent", "status", "attempts", "max_attempts",
                        "digest", "task_id", "run_id", "error")})
    return {
        "plan_id": plan.get("plan_id"), "mode": plan.get("mode"),
        "status": plan.get("status"), "source": plan.get("source"),
        "cursor": plan.get("cursor"), "chapter_cycle": plan.get("chapter_cycle"),
        "budget": plan.get("budget"), "stages": stages,
        "decisions": plan.get("decisions") or [],
        "version": plan.get("version"), "updated_at": plan.get("updated_at"),
        "last_error": plan.get("last_error") or {},
    }


def get_supervisor(store, with_card: bool = False) -> dict:
    cfg = load_cfg(store)
    smode = mode_of(cfg)
    if smode == "off":
        return {"supported": False, "mode": "off", "plan": None, "next_action": None}
    with orch.lock_for(store):
        plan = orch.load_plan(store)
        if plan is None:
            # 未启动：只读接口不虚构一份「运行中」计划误导前端
            return {"supported": True, "mode": smode, "plan": None,
                    "next_action": None, "role_invocations": [], "flow": None}
        orch.reconcile(store, plan, cfg)
        _reconcile_plan_status(store, plan)
        action = sup.next_action(plan, build_ctx(store, plan, cfg))
    return {
        "supported": True,
        "mode": smode,
        "plan": public_plan(plan),
        "next_action": action.to_dict(),
        "role_invocations": orch.load_role_invocations(store)[-30:],
        "flow": project_in_memory(store, plan, cfg),
    }


def public_flow(store, with_card: bool = False) -> dict:
    """给 R27 流程条的同构投影（GET /generation/flow 在 on 模式走这里，只读）。"""
    cfg = load_cfg(store)
    with orch.lock_for(store):
        plan = orch.load_plan(store)
        if plan is None:
            return {"supported": True, "mode": "stepwise", "mode_source": "r32",
                    "flow": None}
        orch.reconcile(store, plan, cfg)
        _reconcile_plan_status(store, plan)
    return {"supported": True, "mode": plan.get("mode"), "mode_source": "r32",
            "flow": project_in_memory(store, plan, cfg)}


def _flow_stage_status(plan: dict, key: str) -> dict:
    from .generation_flow_defs import STAGE_LABELS
    st = orch.stage_by_key(plan, key) or {}
    status = st.get("status") or "pending"
    if key == "chapters":
        loop_started = bool(plan.get("chapters")) or int(
            (plan.get("chapter_cycle") or {}).get("current") or 1) > 1
        if status == "done":
            status = "confirmed"
        elif status == "running":
            status = "running"
        elif status == "pending" and loop_started:
            # 章节循环已在跑（章节记录/游标已推进），阶段状态别停留在「未开始」
            status = "running"
        elif status == "pending" and plan.get("mode") == "stepwise":
            status = "ready"
    return {"key": key, "label": STAGE_LABELS.get(key, key),
            "status": status, "digest": st.get("digest", ""),
            "run_id": st.get("run_id", ""), "task_id": st.get("task_id", ""),
            "error": st.get("error", ""), "reason": st.get("reason", ""),
            "progress": ({"done": max(0, int((plan.get("chapter_cycle") or {}).get("current", 1)) - 1),
                          "total": _total(plan)} if key == "chapters" else None)}


def project_in_memory(store, plan: dict, cfg) -> dict:
    from .generation_flow_defs import STAGE_LABELS
    stages = []
    for key in ("research", "volume", "outline", "world", "chapters"):
        if key == "volume":
            stages.append({"key": key, "label": STAGE_LABELS.get("volume", "卷纲"),
                           "status": "skipped", "reason": "merged_into_outline",
                           "digest": "", "run_id": "", "task_id": "", "error": ""})
        else:
            stages.append(_flow_stage_status(plan, key))
    current = ""
    if plan.get("status") == "done":
        current = ""
    else:
        cc = plan.get("chapter_cycle") or {}
        cur = int(cc.get("current") or 1)
        if store.chapter_path(cur).exists() or (orch.stage_by_key(plan, "chapters") or {}).get("status") == "running":
            current = "chapters"
        else:
            for key in ("research", "outline", "world"):
                if (orch.stage_by_key(plan, key) or {}).get("status") not in ("done", "skipped", "confirmed"):
                    current = key
                    break
    return {"flow_id": f"sup_{plan.get('plan_id')}", "mode": plan.get("mode"),
            "mode_source": "r32", "current": current, "stages": stages,
            "cards": _stepwise_cards(store, plan, cfg),
            "active_run": _active_run(store),
            "created_at": plan.get("created_at"),
            "updated_at": plan.get("updated_at"), "plan_status": plan.get("status")}


def _stepwise_cards(store, plan: dict, cfg) -> list[dict]:
    """分步确认卡片：产物完成后等用户拍板；复用 R27 卡片渲染，不在前端解析产物。"""
    if str(plan.get("mode") or "") != "stepwise" or plan.get("status") in ("blocked", "done", "stopped"):
        return []
    try:
        from .generation_flow_cards import build_card
        from .generation_flow_defs import STAGE_LABELS
        for key in ("research", "outline", "world"):
            st = orch.stage_by_key(plan, key) or {}
            if st.get("status") in ("pending", "running"):
                return []  # 还没产出：不给确认卡，避免出现 0 章「正文」卡
            if st.get("status") == "done" and not st.get("confirmed_at"):
                card = build_card(store, st, cfg)
                return [card] if card else []
        chapter_st = orch.stage_by_key(plan, "chapters") or {}
        if chapter_st.get("status") in ("pending", "ready"):
            flow_st = _flow_stage_status(plan, "chapters")
            card = build_card(store, flow_st, cfg)
            if card:
                card["title"] = STAGE_LABELS.get("chapters", "正文")
                return [card]
    except Exception:
        logger.exception("分步确认卡渲染失败（降级为空）: project=%s", store.dir.name)
    return []


def _project(store, plan: dict, cfg) -> None:
    """旧流程条投影：Supervisor 是 generation_stage.json 的唯一写者（on/shadow）。"""
    try:
        flow = project_in_memory(store, plan, cfg)
        store._write_json(Path(store.dir) / "generation_stage.json", {
            "version": 2, "flow_id": flow["flow_id"], "mode": flow["mode"],
            "mode_source": "r32", "current": flow["current"], "stages": flow["stages"],
            "idempotency": {}, "created_at": flow["created_at"],
            "updated_at": flow["updated_at"],
        })
    except Exception:
        logger.exception("流程投影写入失败（降级）: project=%s", store.dir.name)


def abort(store, user: dict) -> dict:
    cfg = load_cfg(store)
    smode = _mode_or_400(store, cfg)
    if smode not in ("on", "shadow"):
        raise HTTPException(status_code=404, detail="Supervisor 未启用")
    try:
        from . import core
        handle = core._RUNS.get(store.dir.name)
        if handle is not None:
            handle.stop_event.set()
    except Exception:
        logger.exception("停止信号发送失败（降级）: project=%s", store.dir.name)
    with orch.lock_for(store):
        plan = orch.load_plan(store)
        if plan:
            _set_plan_status(plan, "stopped", "user_abort")
            orch.add_decision(plan, str((plan.get("cursor") or {}).get("node_id", "")),
                              "stop", "user", "abort")
            orch.save_plan(store, plan)
            _project(store, plan, cfg)
    return {"aborted": True}


def accept_after_handover(store, user: dict, handover_rec: dict) -> dict:
    """对「修复已耗尽」的工单点放行 = 人工接受当前稿，继续后面流程。

    多次真实修复都没有产出版本改善时，再自动修复只是重复扣费；放行必须能推进，
    否则会原地再转一次人工。质量分与问题清单原样保留，人工的决定进决策日志。
    """
    cfg = load_cfg(store)
    if _mode_or_400(store, cfg) != "on":
        raise HTTPException(status_code=404, detail="Supervisor 未启用")
    trigger = str(handover_rec.get("trigger") or "")
    task_id = str(handover_rec.get("task_id") or "")
    if not trigger.startswith("repair_exhausted"):
        return resume(store, user, task_id)
    ch = int(((handover_rec.get("evidence") or {}).get("quote") or {}).get("ch") or 0)
    with orch.lock_for(store):
        plan = orch.load_plan(store)
        if plan is None:
            raise HTTPException(status_code=409, detail="编排计划不存在")
        no = ch or int((plan.get("cursor") or {}).get("chapter")
                       or (plan.get("chapter_cycle") or {}).get("current") or 0)
        if not no:
            raise HTTPException(status_code=409, detail="工单缺少章号")
        rec = (plan.setdefault("chapters", {})).setdefault(str(no), {
            "status": "done", "step": "done", "attempts": 1, "repairs_used": 0})
        rec.update(status="done", step="done", human_accepted=True,
                   last_issue=f"human_accepted:{trigger}")
        plan["cursor"] = {"node_id": f"chapter:{no}", "chapter": no, "step": "next"}
        orch.add_decision(plan, f"chapter:{no}", "human_accept", "user", trigger,
                          "人工放行：接受当前稿，不再自动修复")
        orch.save_plan(store, plan)
    return resume(store, user, task_id)


def resume(store, user: dict, task_id: str = "") -> dict:
    """工单 approve 后继续编排：清待审、置计划 running、按 next_action 派发下一步。"""
    cfg = load_cfg(store)
    if _mode_or_400(store, cfg) != "on":
        raise HTTPException(status_code=404, detail="Supervisor 未启用")
    try:
        from novel_gen.pipeline import task_state
        tid = task_id or _task_id(store)
        st = task_state.load_state(store, tid) if tid else None
        # 只有真正停在交接点的 task 才需要 clear_pending→RUNNING→DONE；
        # 已收尾(done)/已中止的 task 再做这套流转会撞非法状态，只能降级。
        if st is not None and st.get("state") == task_state.PENDING_REVIEW:
            task_state.clear_pending(store, tid)
            task_state.transition(store, tid, task_state.RUNNING, by="user",
                                  reason="handover approve")
            # 该 task 已停在交接点、不会再被重拾；后续节点各起新 run，
            # 显式关闭旧 task，避免状态机里留下一个永远 RUNNING 的壳。
            task_state.transition(store, tid, task_state.DONE, by="user",
                                  reason="handover approved; supervisor continues in new runs")
            task_state.project_now(store, tid)
            # 收尾同正常 run：删掉 manifest，避免 task.json 被后续判定成「还有 run 在跑」
            try:
                from . import core as _core
                _core._clear_task(store)
            except Exception:
                logger.exception("清理任务标记失败（降级）: project=%s", store.dir.name)
    except Exception:
        logger.exception("放行状态流转失败（降级继续）: project=%s", store.dir.name)
    with orch.lock_for(store):
        plan = orch.load_plan(store)
        if plan is None:
            raise HTTPException(status_code=409, detail="编排计划不存在")
        _set_plan_status(plan, "running", "")
        orch.add_decision(plan, str((plan.get("cursor") or {}).get("node_id", "")),
                          "confirm", "user", "handover_approve")
        orch.save_plan(store, plan)
        action = sup.next_action(plan, build_ctx(store, plan, cfg))
        result = _dispatch(store, plan, action, user, cfg)
        _project(store, plan, cfg)
        return {"started": bool(result.get("started")), "action": result}


def repair_after_handover(store, user: dict, handover_id: str) -> dict:
    """用户对 repair_exhausted 工单点「修复」：关闭工单并派发一次有界修复。"""
    cfg = load_cfg(store)
    if _mode_or_400(store, cfg) != "on":
        raise HTTPException(status_code=404, detail="Supervisor 未启用")
    h = handover.get_handover(store, handover_id) if hasattr(handover, "get_handover") else None
    if h is None:
        rows = handover.list_handovers(store, limit=200)
        h = next((r for r in rows if r.get("handover_id") == handover_id), None)
    if h is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    with orch.lock_for(store):
        plan = orch.load_plan(store)
        if plan is None:
            raise HTTPException(status_code=409, detail="编排计划不存在")
        ch = int(((h.get("evidence") or {}).get("quote") or {}).get("ch") or 0)
        if ch:
            rec = (plan.setdefault("chapters", {})).setdefault(str(ch), {})
            rec["repairs_used"] = 0  # 人工放行一次：重置该章修复计数
        _set_plan_status(plan, "running", "")
        action = sup.Action("repair", f"chapter:{ch or int((plan.get('chapter_cycle') or {}).get('current', 1))}",
                            "writer", "writer.chapter", "repair", {"chapters": [ch]},
                            "user_repair_after_handover")
        result = _dispatch(store, plan, action, user, cfg)
        _project(store, plan, cfg)
        return {"handover_id": handover_id, "action": result}
