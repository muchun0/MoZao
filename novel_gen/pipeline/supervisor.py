# -*- coding: utf-8 -*-
"""Supervisor 决策引擎（REQ-R32 F3 / TECH-R32 §8）。

纯决策：输入计划快照 + 运行结果 + 约束，输出唯一动作（run/wait/repair/fail/done）。
不读写文件、不启动线程、不算钱——副作用全部由 `webapp/supervisor_service.py` 执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import orchestration as orch


@dataclass
class Action:
    kind: str                    # run|wait|repair|fail|done|blocked|confirm
    node_id: str = ""
    role: str = ""
    agent: str = ""
    action: str = ""             # write|review|loremaster|repair（角色内动作）
    params: dict = field(default_factory=dict)
    reason_code: str = ""
    detail: str = ""
    stage_key: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "node_id": self.node_id, "role": self.role,
            "agent": self.agent, "action": self.action, "params": self.params,
            "reason_code": self.reason_code, "detail": self.detail,
            "stage_key": self.stage_key,
        }


def _stage_action(plan: dict, stage: dict, mode: str) -> Action:
    key = str(stage.get("key") or "")
    role = str(stage.get("role") or "")
    agent = str(stage.get("agent") or "")
    node = f"stage:{key}"
    if key == "chapters":
        return Action("run", node, role, agent, "chapter", {}, "stage_ready", "", key)
    # 分步确认的暂停点在「阶段产物完成后」（见 decide_after_run），起跑前不再二次确认：
    # 否则 pending 阶段永远停在 confirm，永远不会有产物可审。
    return Action("run", node, role, agent, "", {"stage": key}, "stage_pending", "", key)


def repair_capacity(plan: dict, chapter_no: int) -> tuple[bool, str]:
    b = plan.get("budget") or {}
    rec = (plan.get("chapters") or {}).get(str(chapter_no)) or {}
    per_ch = int((plan.get("repair_policy") or {}).get("per_chapter", 1) or 0)
    if per_ch and int(rec.get("repairs_used", 0)) >= per_ch:
        return False, "chapter_repair_limit"
    max_r = int(b.get("max_repairs", 0) or 0)
    if max_r and int(b.get("repairs_used", 0)) >= max_r:
        return False, "task_repair_limit"
    return True, ""


def _next_chapter_write(plan: dict, chapter_no: int, reason: str) -> Action:
    rec = (plan.get("chapters") or {}).get(str(chapter_no)) or {}
    rec.update(status="running", step="write")
    plan["chapter_cycle"]["current_step"] = "write"
    plan["cursor"] = {"node_id": f"chapter:{chapter_no}", "chapter": chapter_no, "step": "write"}
    return Action("run", f"chapter:{chapter_no}", "writer", "writer.chapter", "write",
                  {"chapters": [chapter_no]}, reason, "")


def next_action(plan: dict, ctx: dict) -> Action:
    """按计划快照给出下一步动作。ctx 由 Web 层准备（见 supervisor_service）。"""
    cfg = ctx.get("cfg")
    mode = str(plan.get("mode") or "stepwise")
    total = int((plan.get("chapter_cycle") or {}).get("to") or 0)

    if plan.get("status") == "done":
        return Action("done", "", "", "", "", {}, "all_chapters_done")
    if ctx.get("stopped"):
        return Action("wait", "", "", "", "", {}, "stopped")
    if ctx.get("blocking_handover"):
        h = ctx.get("blocking_handover") or {}
        return Action("blocked", "", "", "", "", {},
                      "handover_blocking",
                      str(h.get("reason_code") or h.get("trigger") or ""))
    if ctx.get("pending_review") or plan.get("status") == "blocked":
        return Action("blocked", "", "", "", "", {}, "pending_review")
    if ctx.get("active_run"):
        return Action("wait", "", "", "", "", {}, "active_run")

    exhausted, why = orch.budget_exhausted(plan)
    if exhausted:
        return Action("blocked", "", "", "", "", {}, f"budget_{why}")

    # 章前阶段（research/outline/world）
    for st in plan.get("stages") or []:
        if st.get("key") == "chapters":
            continue
        if st.get("status") in ("pending", "ready", "failed"):
            if st.get("status") == "failed" and int(st.get("attempts", 0)) >= int(st.get("max_attempts", 2)):
                return Action("fail", f"stage:{st.get('key')}", st.get("role", ""),
                              st.get("agent", ""), "", {}, "stage_failed",
                              str(st.get("error") or ""), st.get("key", ""))
            return _stage_action(plan, st, mode)

    chapter_st = orch.stage_by_key(plan, "chapters") or {}
    if chapter_st.get("status") in ("pending", "ready"):
        if mode == "stepwise":
            return Action("confirm", "stage:chapters", "writer", "writer.chapter",
                          "", {}, "stepwise_confirm_required", "", "chapters")
        chapter_st["status"] = "running"
        cc = plan["chapter_cycle"]
        cc["from"] = max(1, int(cc.get("current") or 1))
        # 阶段状态可能是历史遗留的 pending（如放行工单/切模式时）：统一交给
        # chapter_action 按产物判续推，避免把已完成章按「开跑」重写一遍。
        return chapter_action(plan, ctx, total)
    if chapter_st.get("status") == "failed":
        return Action("fail", "stage:chapters", "writer", "writer.chapter", "", {},
                      "chapters_failed", "", "chapters")

    return chapter_action(plan, ctx, total)


def chapter_action(plan: dict, ctx: dict, total: int) -> Action:
    """章节循环游标决策：写→审→（修复）→下一章。"""
    cfg = ctx.get("cfg")
    cc = plan.setdefault("chapter_cycle", {})
    no = int(cc.get("current") or 1)
    if no > total:
        return Action("done", "", "", "", "", {}, "all_chapters_done")
    rec = (plan.get("chapters") or {}).get(str(no)) or {}
    exists = ctx.get("chapter_exists")
    if exists is None:
        exists = bool(getattr(ctx.get("store"), "chapter_path", None) and
                      ctx["store"].chapter_path(no).exists())

    quality_impl = bool(getattr(getattr(cfg, "agent_guard", None), "quality_check", True))
    if not exists:
        return _next_chapter_write(plan, no, "chapter_missing")

    has_review = bool(ctx.get("review_exists"))
    if quality_impl and not has_review:
        rec.update(status="running", step="review")
        plan["cursor"] = {"node_id": f"chapter:{no}", "chapter": no, "step": "review"}
        return Action("run", f"chapter:{no}", "reviewer", "writer.chapter", "review",
                      {"chapters": [no], "action": "review"}, "review_missing", "")

    passed = ctx.get("review_passed")
    if rec.get("human_accepted"):
        # 人工放行「修复已耗尽」的章：接受当前稿，流程必须往前走，
        # 否则只会原地再转一次人工（真跑多次修复均无改善）。
        passed = True
    if passed is None:
        passed, _ = (True, {})
    if not passed:
        ok, why = repair_capacity(plan, no)
        if not ok:
            return Action("fail", f"chapter:{no}", "writer", "writer.chapter", "", {},
                          f"repair_exhausted:{why}", "")
        return Action("repair", f"chapter:{no}", "writer", "writer.chapter", "repair",
                      {"chapters": [no]}, "quality_gate_fail", "")

    # 通过：进入下一章（ledger_commit 已在写章 run 内完成）；
    # 已写且质检通过的章直接跳过——产物是唯一权威，放行/重启后不能重写已有章。
    orch.advance_chapter(plan, total)
    store = ctx.get("store")
    while True:
        nxt = int(plan["chapter_cycle"].get("current") or 1)
        if nxt > total or plan.get("status") == "done":
            return Action("done", "", "", "", "", {}, "all_chapters_done")
        if _chapter_artifact_done(store, nxt, quality_impl) or \
                ((plan.get("chapters") or {}).get(str(nxt)) or {}).get("human_accepted"):
            orch.advance_chapter(plan, total)
            continue
        if quality_impl and _chapter_reviewed_failed(store, nxt):
            # 已写但质检没过：走修复，不能整章重写（会白扔一次钱且丢掉已写的正文）
            rec = (plan.get("chapters") or {}).get(str(nxt)) or {}
            rec.update(status="running", step="repair")
            plan["cursor"] = {"node_id": f"chapter:{nxt}", "chapter": nxt, "step": "repair"}
            plan["chapter_cycle"]["current"] = nxt
            plan["chapter_cycle"]["current_step"] = "repair"
            ok, why = repair_capacity(plan, nxt)
            if not ok:
                return Action("fail", f"chapter:{nxt}", "writer", "writer.chapter", "", {},
                              f"repair_exhausted:{why}", "")
            return Action("repair", f"chapter:{nxt}", "writer", "writer.chapter", "repair",
                          {"chapters": [nxt]}, "quality_gate_fail", "")
        return _next_chapter_write(plan, nxt, "chapter_advance")


def _chapter_reviewed_failed(store, no: int) -> bool:
    if store is None:
        return False
    try:
        rec = next((r for r in (store.load_review() or [])
                    if int(r.get("chapter") or 0) == no), None)
        return bool(rec) and not bool((rec.get("quality_check") or {}).get("passed", True))
    except Exception:
        return False


def _chapter_artifact_done(store, no: int, quality_impl: bool) -> bool:
    """正文在盘且质检通过即视为该章已完成（只读判断，不写产物）。"""
    if store is None:
        return False
    try:
        if not store.chapter_path(no).exists():
            return False
        if not quality_impl:
            return True
        rec = next((r for r in (store.load_review() or [])
                    if int(r.get("chapter") or 0) == no), None)
        if rec is None:
            return False
        return bool((rec.get("quality_check") or {}).get("passed", True))
    except Exception:
        return False


def repair_scope_text(review: dict) -> str:
    """从 review.json 记录确定性派生修复范围（拼 prompt 只此一处）。"""
    issues = (review or {}).get("issues") or []
    lines = []
    for item in issues[:5]:
        if not isinstance(item, dict):
            continue
        sev = str(item.get("severity") or item.get("level") or "")
        typ = str(item.get("type") or "")
        detail = str(item.get("detail") or item.get("description") or "")[:200]
        line = f"- 【{sev or '问题'}】{typ}：{detail}"
        sug = str(item.get("fix_suggestion") or "").strip()
        if sug:
            line += f"（建议：{sug[:120]}）"
        lines.append(line)
    return "\n".join(lines)


def decide_after_run(plan: dict, node_id: str, role: str, run_status: str,
                     cfg, review: dict | None, missing: list[str],
                     mode: str = "auto") -> Action:
    """run 结束后的下一动作（不启动 run，只做决策与计划状态流转）。"""
    if node_id.startswith("stage:"):
        key = node_id.split(":", 1)[1]
        st = orch.stage_by_key(plan, key) or {}
        if run_status in ("pending_review", "stopped"):
            return Action("blocked", node_id, role, "", "", {}, run_status)
        if run_status != "ok" or missing:
            st["attempts"] = int(st.get("attempts", 0)) + 1
            if int(st["attempts"]) >= int(st.get("max_attempts", 2)):
                st["status"] = "failed"
                st["error"] = "run_failed" if run_status != "ok" else "outputs_missing"
                return Action("fail", node_id, role, "", "", {}, st["error"])
            return Action("run", node_id, role, st.get("agent", ""), "",
                          {"stage": key}, "stage_retry")
        st.update(status="done", error="")
        if key == "research":
            return Action("run", "stage:outline", "planner", "architect.outline",
                          "", {"stage": "outline"}, "stage_advance")
        if mode == "stepwise":
            return Action("confirm", "stage:outline" if key != "outline" else "stage:world",
                          "planner" if key != "outline" else "worldbuilder",
                          "architect.outline" if key != "outline" else "architect.world",
                          "", {}, "stepwise_confirm_required")
        nxt = {"outline": ("world", "worldbuilder", "architect.world"),
               "world": ("chapters", "writer", "writer.chapter")}.get(key)
        if not nxt:
            return Action("done", "", "", "", "", {}, "stages_done")
        return Action("run", f"stage:{nxt[0]}", nxt[1], nxt[2], "", {"stage": nxt[0]},
                      "stage_advance")

    # 章节节点
    no = orch.chapter_number_from_node(node_id) or int(
        (plan.get("chapter_cycle") or {}).get("current") or 1)
    rec = (plan.setdefault("chapters", {})).setdefault(str(no), {
        "status": "pending", "step": "write", "attempts": 0, "repairs_used": 0})
    if run_status in ("pending_review", "stopped"):
        return Action("blocked", node_id, role, "", "", {}, run_status)
    if run_status != "ok" or missing:
        rec["attempts"] = int(rec.get("attempts", 0)) + 1
        if int(rec["attempts"]) >= 2:
            rec["status"] = "failed"
            rec["last_issue"] = "run_failed" if run_status != "ok" else "outputs_missing"
            return Action("fail", node_id, role, "", "", {}, rec["last_issue"])
        return Action("run", node_id, role, "writer.chapter",
                      "review" if role == "reviewer" else "write",
                      {"chapters": [no]}, "chapter_retry")

    if role == "reviewer":
        passed, _ = _passed(cfg, review)
        if not passed:
            return _repair_or_fail(plan, no, "quality_gate_fail")
        orch.advance_chapter(plan, int((plan.get("chapter_cycle") or {}).get("to") or 0))
        return _after_chapter(plan)

    if role == "writer":
        quality_on = bool(getattr(getattr(cfg, "agent_guard", None), "quality_check", True))
        if quality_on and not review:
            rec["step"] = "review"
            return Action("run", node_id, "reviewer", "writer.chapter", "review",
                          {"chapters": [no]}, "review_missing")
        passed, _ = _passed(cfg, review)
        if not passed:
            return _repair_or_fail(plan, no, "quality_gate_fail")
        orch.advance_chapter(plan, int((plan.get("chapter_cycle") or {}).get("to") or 0))
        return _after_chapter(plan)

    if role == "loremaster":
        orch.advance_chapter(plan, int((plan.get("chapter_cycle") or {}).get("to") or 0))
        return _after_chapter(plan)

    return _after_chapter(plan)


def _passed(cfg, review: dict | None) -> tuple[bool, dict]:
    if not bool(getattr(getattr(cfg, "agent_guard", None), "quality_check", True)):
        return True, review or {}
    if not review:
        return False, {}
    qc = (review or {}).get("quality_check") or {}
    return bool(qc.get("passed", True)), review or {}


def _repair_or_fail(plan: dict, no: int, reason: str) -> Action:
    ok, why = repair_capacity(plan, no)
    if not ok:
        return Action("fail", f"chapter:{no}", "writer", "writer.chapter", "", {},
                      f"repair_exhausted:{why}", "")
    rec = (plan.get("chapters") or {}).get(str(no)) or {}
    rec["step"] = "repair"
    rec["last_issue"] = reason
    return Action("repair", f"chapter:{no}", "writer", "writer.chapter", "repair",
                  {"chapters": [no]}, "quality_gate_fail", "")


def _after_chapter(plan: dict) -> Action:
    if plan.get("status") == "done":
        return Action("done", "", "", "", "", {}, "all_chapters_done")
    cc = plan.get("chapter_cycle") or {}
    no = int(cc.get("current") or 1)
    return _next_chapter_write(plan, no, "chapter_advance")


def mark_repair_used(plan: dict, chapter_no: int) -> None:
    rec = (plan.setdefault("chapters", {})).setdefault(str(chapter_no), {
        "status": "running", "step": "repair", "attempts": 0, "repairs_used": 0})
    rec["repairs_used"] = int(rec.get("repairs_used", 0)) + 1
    rec["step"] = "review"
    orch.update_budget(plan, steps=1, repairs=1)

