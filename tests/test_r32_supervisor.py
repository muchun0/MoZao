# -*- coding: utf-8 -*-
"""REQ-R32 固化 Supervisor：角色契约 / 计划模型 / 决策引擎 / 开关门禁 单测。

纯逻辑 + 真实 ProjectStore（临时目录），不触 LLM、不触 DB、不发线程。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

from novel_gen.config import NovelConfig
from novel_gen.pipeline import orchestration as orch
from novel_gen.pipeline import roles, supervisor as sup, handover
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter


def _store(tmp_path: Path, *, quality: bool = False, chapters: int = 3,
           mode: str = "auto") -> ProjectStore:
    cfg = NovelConfig(title="R32单测")
    cfg.agent_guard.quality_check = quality
    cfg.supervisor_repair_rounds = 1
    cfg.supervisor_max_repairs = 3
    store = ProjectStore.create(tmp_path / "p", cfg)
    store._write_json(store.outline_path(), {
        "title": "R32单测",
        "chapters": [{"title": f"第{i}章", "goal": f"目标{i}"}
                     for i in range(1, chapters + 1)],
    })
    return store


def _cfg(store: ProjectStore, *, quality: bool = False) -> NovelConfig:
    """加载项目配置；quality_check 可能被测试环境变量覆盖，这里显式定值。"""
    cfg = store.load_config()
    cfg.agent_guard.quality_check = quality
    return cfg


def test_role_registry_contract():
    assert roles.validate_dispatch("writer", "writer.chapter", "chapter:3").agent == \
        "writer.chapter"
    with pytest.raises(roles.RoleContractError):
        roles.validate_dispatch("writer", "architect.world", "chapter:3")
    with pytest.raises(roles.RoleContractError):
        roles.validate_dispatch("planner", "architect.outline", "chapter:3")
    with pytest.raises(roles.RoleContractError):
        roles.validate_dispatch("", "writer.chapter", "chapter:3")
    with pytest.raises(roles.RoleContractError):
        roles.validate_dispatch("reviewer", "architect.outline", "chapter:3")
    # 嵌入角色不可外部派发，内部派发放行
    with pytest.raises(roles.RoleContractError):
        roles.validate_dispatch("reviewer", "writer.chapter", "chapter:3")
    assert roles.validate_dispatch("reviewer", "writer.chapter", "chapter:3",
                                   internal=True).agent == "writer.chapter"
    # 老客户端不带 role：按 agent 推断后再走同一套契约
    assert roles.infer_role("writer.chapter") == "writer"
    assert roles.validate_dispatch(roles.infer_role("architect.volume"),
                                   "architect.volume", "stage:outline").agent == \
        "architect.volume"


def test_outcome_check_catches_missing_artifact(tmp_path):
    store = _store(tmp_path)
    assert roles.outcome_check(store, "writer", "chapter:1") == ["chapters/1.json"]
    store.save_chapter(Chapter(no=1, title="第1章", content="正文", summary="摘要", goal="目标1"))
    assert roles.outcome_check(store, "writer", "chapter:1") == []


def test_plan_build_and_cursor(tmp_path):
    store = _store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="auto")
    assert [s["key"] for s in plan["stages"]] == ["research", "outline", "world", "chapters"]
    assert plan["chapter_cycle"]["to"] == 3
    assert plan["cursor"]["node_id"] == "chapter:1"
    assert plan["budget"]["max_steps"] >= 200
    path = orch.plan_path(store)
    assert not path.exists()  # build_plan 不落盘，ensure_plan 才写

    plan = orch.ensure_plan(store, cfg, mode="auto")
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_plan_save_is_atomic_and_versioned(tmp_path):
    store = _store(tmp_path)
    cfg = store.load_config()
    plan = orch.ensure_plan(store, cfg, mode="auto")
    v1 = plan["version"]
    plan = orch.ensure_plan(store, cfg, mode="stepwise")  # 重建
    assert plan["version"] >= v1
    assert plan["mode"] == "stepwise"
    assert not list(store.dir.glob("orchestration.json.tmp*"))


def test_decisions_ring_capped(tmp_path):
    store = _store(tmp_path)
    cfg = store.load_config()
    plan = orch.ensure_plan(store, cfg, mode="auto")
    for i in range(205):
        orch.add_decision(plan, f"chapter:{i % 3 + 1}", "decision", "supervisor", f"r{i}")
    assert len(plan["decisions"]) == orch.DECISIONS_MAX
    assert plan["decisions"][-1]["reason_code"] == "r204"


def test_next_action_stage_order(tmp_path):
    store = _store(tmp_path)
    cfg = store.load_config()
    plan = orch.ensure_plan(store, cfg, mode="auto")
    action = sup.next_action(plan, {"cfg": cfg, "store": store})
    assert action.kind == "run" and action.node_id == "stage:world"

    orch.stage_by_key(plan, "world")["status"] = "done"
    action = sup.next_action(plan, {"cfg": cfg, "store": store,
                                    "chapter_exists": False,
                                    "review_exists": False, "review_passed": True})
    assert action.kind == "run" and action.action == "write"
    assert action.params["chapters"] == [1]


def test_stepwise_requires_confirm(tmp_path):
    store = _store(tmp_path)
    cfg = store.load_config()
    plan = orch.ensure_plan(store, cfg, mode="stepwise")
    orch.stage_by_key(plan, "world")["status"] = "done"
    action = sup.next_action(plan, {"cfg": cfg, "store": store})
    assert action.kind == "confirm" and action.stage_key == "chapters"


def test_chapter_loop_advances_and_done(tmp_path):
    store = _store(tmp_path)
    cfg = store.load_config()
    plan = orch.ensure_plan(store, cfg, mode="auto")
    orch.stage_by_key(plan, "world")["status"] = "done"
    plan["chapter_cycle"]["current"] = 1
    plan["chapter_cycle"]["current_step"] = "write"
    for no in (1, 2, 3):
        store.save_chapter(Chapter(no=no, title=f"第{no}章", content="正文" * 100,
                                   summary="摘要", goal=f"目标{no}"))
        action = sup.decide_after_run(plan, f"chapter:{no}", "writer", "ok", cfg,
                                      None, [], mode="auto")
    assert action.kind == "done"
    assert plan["status"] == "done"
    assert plan["chapter_cycle"]["current"] == 3


def test_review_missing_triggers_reviewer(tmp_path):
    store = _store(tmp_path, quality=True)
    cfg = _cfg(store, quality=True)
    plan = orch.ensure_plan(store, cfg, mode="auto")
    orch.stage_by_key(plan, "world")["status"] = "done"
    orch.stage_by_key(plan, "chapters")["status"] = "running"
    store.save_chapter(Chapter(no=1, title="第1章", content="正文" * 100,
                               summary="摘要", goal="目标1"))
    plan["chapter_cycle"]["current"] = 1
    action = sup.next_action(plan, {"cfg": cfg, "store": store,
                                    "chapter_exists": True, "review_exists": False})
    assert action.kind == "run" and action.role == "reviewer" and action.action == "review"


def test_quality_fail_repair_then_exhaust(tmp_path):
    store = _store(tmp_path, quality=True)
    cfg = _cfg(store, quality=True)
    plan = orch.ensure_plan(store, cfg, mode="auto")
    orch.stage_by_key(plan, "world")["status"] = "done"
    plan["chapter_cycle"]["current"] = 1
    review = {"chapter": 1, "quality_check": {"passed": False}}
    store.save_review_chapter(1, review)
    action = sup.decide_after_run(plan, "chapter:1", "writer", "ok", cfg, review, [])
    assert action.kind == "repair"
    sup.mark_repair_used(plan, 1)
    action = sup.decide_after_run(plan, "chapter:1", "writer", "ok", cfg, review, [])
    assert action.kind == "fail"
    assert action.reason_code.startswith("repair_exhausted")


def test_budget_exhausted_blocks(tmp_path):
    store = _store(tmp_path)
    cfg = store.load_config()
    plan = orch.ensure_plan(store, cfg, mode="auto")
    plan["budget"]["steps_used"] = plan["budget"]["max_steps"]
    action = sup.next_action(plan, {"cfg": cfg, "store": store})
    assert action.kind == "blocked" and action.reason_code == "budget_max_steps"


def test_blocking_handover_blocks_everything(tmp_path):
    store = _store(tmp_path)
    cfg = store.load_config()
    plan = orch.ensure_plan(store, cfg, mode="auto")
    action = sup.next_action(plan, {"cfg": cfg, "store": store,
                                    "blocking_handover": {"trigger": "rule_hit"}})
    assert action.kind == "blocked"


def test_review_single_writer_merge_and_atomic(tmp_path):
    store = _store(tmp_path)
    store.save_review_chapter(1, {"chapter": 1, "issues": [{"type": "小改"}],
                                  "quality_check": {"passed": True}})
    store.save_review_chapter(1, {"chapter": 1, "issues": [{"type": "读者-节奏"}],
                                  "reader_hook_score": 4}, merge=True)
    rows = store.load_review()
    assert len(rows) == 1
    assert len(rows[0]["issues"]) == 2
    assert rows[0]["reader_hook_score"] == 4
    assert not list(store.dir.glob("review.json.tmp*"))


def test_review_concurrent_writes_no_lost_rows(tmp_path):
    store = _store(tmp_path)
    threads = [threading.Thread(target=store.save_review_chapter,
                                args=(n, {"chapter": n, "issues": [], "mark": n}))
               for n in range(1, 21)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = store.load_review()
    assert len(rows) == 20
    assert {r["chapter"] for r in rows} == set(range(1, 21))


def test_handover_repair_trigger_registered(tmp_path):
    assert "repair_exhausted" in handover.TRIGGERS
    assert "repair" in handover.OPTIONS
    store = _store(tmp_path)
    h = handover.create_repair_exhausted_handover(store, "t1", 2, "复核仍不通过")
    assert h.get("blocking") is True
    assert h.get("status") == handover.OPEN


def test_supervisor_service_off_and_dependency_gate(tmp_path):
    from webapp import supervisor_service as sv

    store = _store(tmp_path)
    off = sv.get_supervisor(store)
    assert off["supported"] is False and off["mode"] == "off"

    cfg = store.load_config()
    cfg.supervisor_mode = "on"
    cfg.agent_guard.quality_check = True
    cfg.task_state_machine = False
    with pytest.raises(HTTPException) as exc:
        sv.check_dependencies(cfg)
    assert exc.value.status_code == 400
    assert "任务状态机" in str(exc.value.detail)

    cfg.task_state_machine = True
    cfg.handover_v1 = True
    sv.check_dependencies(cfg)  # 不抛

    # shadow 只依赖任务状态机（TECH §10.1），不要求 handover_v1 / quality_check
    cfg.supervisor_mode = "shadow"
    cfg.task_state_machine = False
    with pytest.raises(HTTPException) as exc:
        sv.check_shadow_dependencies(cfg)
    assert exc.value.status_code == 400 and "任务状态机" in str(exc.value.detail)
    cfg.task_state_machine = True
    sv.check_shadow_dependencies(cfg)  # 不抛


def test_supervisor_get_on_mode_readonly(tmp_path):
    from webapp import supervisor_service as sv

    store = _store(tmp_path)
    cfg = store.load_config()
    cfg.supervisor_mode = "on"
    cfg.agent_guard.quality_check = True
    cfg.task_state_machine = True
    cfg.handover_v1 = True
    store.save_config(cfg)
    assert not orch.plan_path(store).exists()
    data = sv.get_supervisor(store)
    assert data["supported"] is True
    # 未启动不虚构计划：避免前端把「未开始」误显示成「运行中/等待确认」
    assert data["plan"] is None
    assert data["next_action"] is None
    assert data["flow"] is None
    assert not orch.plan_path(store).exists()  # GET 不落盘


def test_shadow_start_does_not_run(tmp_path):
    from webapp import supervisor_service as sv

    store = _store(tmp_path)
    cfg = store.load_config()
    cfg.supervisor_mode = "shadow"
    cfg.task_state_machine = True
    store.save_config(cfg)
    out = sv.start(store, {"username": "张阅"}, mode="auto")
    assert out["supported"] is True and out["started"] is False
    assert not orch.plan_path(store).exists()


def _on_store(tmp_path, chapters: int = 3) -> ProjectStore:
    store = _store(tmp_path, chapters=chapters)
    cfg = store.load_config()
    cfg.supervisor_mode = "on"
    cfg.task_state_machine = True
    cfg.handover_v1 = True
    cfg.agent_guard.quality_check = True
    cfg.auto_rewrite_rounds = 0
    store.save_config(cfg)
    return store


def test_stepwise_start_runs_outline_before_confirm(tmp_path, monkeypatch):
    """分步启动必须先产出大纲（跑 run），不能停在无产物的 confirm 死循环。"""
    from webapp import supervisor_service as sv, generate_service

    store = _on_store(tmp_path)
    store.outline_path().unlink()  # 真空项目：尚无大纲产物
    calls: list[str] = []

    def fake_run(store_, req, user, internal=None):
        calls.append(req.agent)
        return {"run_id": "run-1", "task_id": "t-1"}

    monkeypatch.setattr(generate_service, "start_agent_run", fake_run)
    out = sv.start(store, {"username": "张阅"}, mode="stepwise")
    assert out["next_action"]["kind"] == "run"
    assert out["next_action"]["node_id"] == "stage:outline"
    assert calls == ["architect.outline"]
    plan = orch.load_plan(store)
    assert (orch.stage_by_key(plan, "outline") or {}).get("status") == "running"


def test_stepwise_projection_has_confirm_card(tmp_path):
    """大纲落盘后，分步计划要在旧流程条投影里给出可点的确认卡。"""
    from webapp import supervisor_service as sv

    store = _on_store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="stepwise")
    st = orch.stage_by_key(plan, "outline")
    st["status"] = "done"
    orch.save_plan(store, plan)
    flow = sv.project_in_memory(store, plan, cfg)
    assert flow["cards"] and flow["cards"][0]["stage"] == "outline"
    assert "continue" in flow["cards"][0]["actions"]


def test_stepwise_no_card_while_outline_running(tmp_path):
    """大纲还在跑时不能提前弹「正文确认卡」（否则出现全书 0 章的假卡）。"""
    from webapp import supervisor_service as sv

    store = _on_store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="stepwise")
    orch.stage_by_key(plan, "outline")["status"] = "running"
    orch.save_plan(store, plan)
    flow = sv.project_in_memory(store, plan, cfg)
    assert flow["cards"] == []


def test_on_run_finished_real_decision_chain(tmp_path, monkeypatch):
    """真实回调链：run 结束 → 决策落盘 → 分步确认卡 → 确认后派发下一阶段。

    这里不 mock orch.add_decision，专门防「参数签名写错把回调打断」这类真实运行才暴露的问题。
    """
    from webapp import supervisor_service as sv, generate_service

    store = _on_store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="stepwise")
    orch.stage_by_key(plan, "outline")["status"] = "running"
    orch.save_plan(store, plan)
    out = sv.on_run_finished(store, "architect.outline", "run-1", "ok",
                             node_id="stage:outline", role="planner")
    assert out["handled"] is True and out["mode"] == "on"
    plan = orch.load_plan(store)
    assert (orch.stage_by_key(plan, "outline") or {}).get("status") == "done"
    assert plan["decisions"][-1]["kind"] in ("decision", "run_finished")
    assert (plan.get("last_error") or {}).get("code") != "run_finished"
    flow = sv.project_in_memory(store, plan, cfg)
    assert flow["cards"] and flow["cards"][0]["stage"] == "outline"

    calls: list[str] = []
    monkeypatch.setattr(generate_service, "start_agent_run",
                        lambda store_, req, user, internal=None:
                        calls.append(req.agent) or {"run_id": "r2", "task_id": "t2"})
    sv.confirm(store, {"username": "张阅"}, "outline", "continue",
               digest=(orch.stage_by_key(plan, "outline") or {}).get("digest", ""))
    assert calls == ["architect.world"]


def test_set_mode_auto_advances_after_confirm(tmp_path, monkeypatch):
    """on 模式切全自动要立即推进下一阶段（切模式不能只是写个字段）。"""
    from webapp import supervisor_service as sv, generate_service

    store = _on_store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="stepwise")
    orch.stage_by_key(plan, "outline")["status"] = "done"
    orch.save_plan(store, plan)
    calls: list[str] = []
    monkeypatch.setattr(generate_service, "start_agent_run",
                        lambda store_, req, user, internal=None:
                        calls.append(req.agent) or {"run_id": "r", "task_id": "t"})
    out = sv.set_mode(store, {"username": "张阅"}, "auto")
    assert out["mode"] == "auto"
    assert calls == ["architect.world"]


def test_active_run_ignores_finished_task_manifest(tmp_path):
    """task.json 在待审/收尾后仍会保留，不能因此把计划永远判成 active_run。"""
    from webapp import supervisor_service as sv

    store = _on_store(tmp_path)
    task = Path(store.dir) / "task.json"
    assert sv._active_run(store) is False  # 无 manifest 无线程
    store._write_json(task, {"task_id": "t1", "state": "running", "wait_human": False})
    assert sv._active_run(store) is True
    store._write_json(task, {"task_id": "t1", "state": "pending_review", "wait_human": True})
    assert sv._active_run(store) is False
    store._write_json(task, {"task_id": "t1", "state": "done"})
    assert sv._active_run(store) is False


def test_chapter_advance_skips_already_satisfied_chapter(tmp_path, monkeypatch):
    """产物即权威：已写且质检通过的章不再重写（放行/续跑不能重复花钱）。"""
    from webapp import supervisor_service as sv, generate_service

    store = _on_store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="auto")
    for key in ("outline", "world"):
        orch.stage_by_key(plan, key)["status"] = "done"
    orch.stage_by_key(plan, "chapters")["status"] = "pending"
    for no in (1, 2, 3):
        store._write_json(store.chapter_path(no), {"no": no, "title": f"第{no}章", "content": "正文"})
        store.save_review_chapter(no, {"chapter": no, "quality_check": {"passed": True}})
    plan["chapters"] = {"1": {"status": "done", "step": "done"},
                        "2": {"status": "running", "step": "write"}}
    plan["chapter_cycle"].update({"from": 1, "to": 3, "current": 2, "current_step": "write"})
    plan["cursor"] = {"node_id": "chapter:2", "chapter": 2, "step": "write"}
    orch.save_plan(store, plan)
    store._write_json(Path(store.dir) / "task.json", {"task_id": "r32_ch2", "state": "done"})

    calls: list = []
    monkeypatch.setattr(generate_service, "start_agent_run",
                        lambda *a, **kw: calls.append(a) or {"run_id": "r", "task_id": "t"})
    out = sv.resume(store, {"username": "张阅"}, "r32_ch2")
    assert out["started"] is False
    assert calls == []
    plan = orch.load_plan(store)
    assert plan["status"] == "done"


def test_sync_chapters_keeps_running_when_review_failed(tmp_path):
    """章节审过了但没过门：不能因为「产物在盘」就投影成 done。"""
    store = _on_store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="auto")
    for no in (1, 2):
        store._write_json(store.chapter_path(no), {"no": no, "title": f"第{no}章", "content": "正文"})
        store.save_review_chapter(no, {"chapter": no, "quality_check": {"passed": True}})
    store._write_json(store.chapter_path(3), {"no": 3, "title": "第3章", "content": "正文"})
    store.save_review_chapter(3, {"chapter": 3, "quality_check": {"passed": False}})
    orch.sync_chapters(store, plan, cfg)
    assert plan["status"] != "done"
    assert plan["chapters"]["3"]["status"] == "running"
    assert plan["chapters"]["3"]["step"] == "repair"


def test_supervisor_read_reconciles_blocked(tmp_path):
    """有阻塞工单又没在跑时，读接口要把计划收敛成 blocked，不能一直显示运行中。"""
    from webapp import supervisor_service as sv

    store = _on_store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="auto")
    plan["status"] = "running"
    orch.save_plan(store, plan)
    handover.create_handover(store, task_id="r32_x", trigger="repair_exhausted",
                             severity="high", blocking=True, evidence={},
                             dedupe_key="ch:3:repair:limit")
    sv.get_supervisor(store)
    disk = orch.load_plan(store)
    assert disk["status"] == "blocked"


def test_dispatch_fail_persists_blocked(tmp_path, monkeypatch):
    """转人工必须落盘：否则刷新后计划还显示「运行中」。"""
    from webapp import supervisor_service as sv, generate_service

    store = _on_store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="auto")
    orch.save_plan(store, plan)
    action = sv.sup.Action(kind="fail", node_id="chapter:3", reason_code="repair_exhausted:chapter_repair_limit")
    monkeypatch.setattr(generate_service, "start_agent_run",
                        lambda *a, **kw: {"run_id": "r", "task_id": "t"})
    sv._dispatch(store, plan, action, {"username": "张阅"}, cfg)
    disk = orch.load_plan(store)
    assert disk["status"] == "blocked"
    assert (disk.get("last_error") or {}).get("code").startswith("repair_exhausted")


def test_approve_repair_exhausted_accepts_chapter(tmp_path, monkeypatch):
    """修复耗尽工单点放行 = 人工接受当前稿，必须推进到下一章/收尾，不能原地再转人工。"""
    from webapp import supervisor_service as sv, generate_service

    store = _on_store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="auto")
    for key in ("outline", "world"):
        orch.stage_by_key(plan, key)["status"] = "done"
    orch.stage_by_key(plan, "chapters")["status"] = "running"
    for no in (1, 2):
        store._write_json(store.chapter_path(no), {"no": no, "title": f"第{no}章", "content": "正文"})
        store.save_review_chapter(no, {"chapter": no, "quality_check": {"passed": True}})
    store._write_json(store.chapter_path(3), {"no": 3, "title": "第3章", "content": "正文"})
    store.save_review_chapter(3, {"chapter": 3, "quality_check": {"passed": False}})
    plan["chapters"] = {"1": {"status": "done", "step": "done"},
                        "2": {"status": "done", "step": "done"},
                        "3": {"status": "running", "step": "repair",
                              "attempts": 1, "repairs_used": 1}}
    plan["chapter_cycle"].update({"from": 1, "to": 3, "current": 3, "current_step": "repair"})
    plan["cursor"] = {"node_id": "chapter:3", "chapter": 3, "step": "repair"}
    plan["status"] = "blocked"
    orch.save_plan(store, plan)
    h = handover.create_repair_exhausted_handover(store, "r32_x", 3, "自动修复达上限")

    monkeypatch.setattr(generate_service, "start_agent_run",
                        lambda *a, **kw: {"run_id": "r", "task_id": "t"})
    res = handover.resolve(store, h["handover_id"], "approve", by="admin")
    out = sv.accept_after_handover(store, {"username": "张阅"}, res)
    assert "started" in out
    plan = orch.load_plan(store)
    assert plan["chapters"]["3"]["human_accepted"] is True
    action = sv.sup.next_action(plan, sv.build_ctx(store, plan, cfg))
    assert action.kind == "done"


def test_chapter_advance_repairs_instead_of_rewriting(tmp_path):
    """已写但质检没过：续跑应派修复，而不是整章重写。"""
    from webapp import supervisor_service as sv

    store = _on_store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="auto")
    for key in ("outline", "world"):
        orch.stage_by_key(plan, key)["status"] = "done"
    orch.stage_by_key(plan, "chapters")["status"] = "running"
    for no in (1, 2):
        store._write_json(store.chapter_path(no), {"no": no, "title": f"第{no}章", "content": "正文"})
        store.save_review_chapter(no, {"chapter": no, "quality_check": {"passed": True}})
    store._write_json(store.chapter_path(3), {"no": 3, "title": "第3章", "content": "正文"})
    store.save_review_chapter(3, {"chapter": 3, "quality_check": {"passed": False}})
    plan["chapters"] = {"1": {"status": "done", "step": "done"},
                        "2": {"status": "running", "step": "write"}}
    plan["chapter_cycle"].update({"from": 1, "to": 3, "current": 2, "current_step": "write"})
    plan["cursor"] = {"node_id": "chapter:2", "chapter": 2, "step": "write"}
    orch.save_plan(store, plan)
    action = sv.sup.next_action(plan, sv.build_ctx(store, plan, cfg))
    assert action.kind == "repair" and action.node_id == "chapter:3"
    assert action.reason_code == "quality_gate_fail"


def test_sync_chapters_keeps_running_with_blocking_handover(tmp_path):
    """有阻塞工单时不能投影成「全流程已完成」，否则用户看不到还没放行。"""
    store = _on_store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="auto")
    for no in (1, 2, 3):
        store._write_json(store.chapter_path(no), {"no": no, "title": f"第{no}章", "content": "正文"})
        store.save_review_chapter(no, {"chapter": no, "quality_check": {"passed": True}})
    handover.create_handover(store, task_id="r32_x", trigger="rule_hit", severity="high",
                             blocking=True, evidence={}, dedupe_key="ch:3:rule:test")
    orch.sync_chapters(store, plan, cfg)
    assert plan["status"] != "done"
    assert (orch.stage_by_key(plan, "chapters") or {}).get("status") == "running"


def test_on_run_finished_attributes_direct_chapter_run(tmp_path, monkeypatch):
    """页面直跑的章节 run（无 node）要按产物记到真实章号，不能记到旧游标章。"""
    from webapp import supervisor_service as sv, generate_service

    store = _on_store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="auto")
    for key in ("outline", "world"):
        orch.stage_by_key(plan, key)["status"] = "done"
    orch.stage_by_key(plan, "chapters")["status"] = "running"
    plan["chapter_cycle"].update({"from": 1, "to": 3, "current": 2, "current_step": "write"})
    plan["cursor"] = {"node_id": "chapter:2", "chapter": 2, "step": "write"}
    orch.save_plan(store, plan)
    for no in (1, 2, 3):
        store._write_json(store.chapter_path(no), {"no": no, "title": f"第{no}章", "content": "正文"})
        store.save_review_chapter(no, {"chapter": no, "quality_check": {"passed": True}})
    monkeypatch.setattr(generate_service, "start_agent_run",
                        lambda *a, **kw: {"run_id": "r", "task_id": "t"})
    out = sv.on_run_finished(store, "writer.chapter", "run-direct", "ok")
    assert out["handled"] is True
    plan = orch.load_plan(store)
    assert any(d.get("node") == "chapter:3" for d in plan["decisions"])


def test_handover_approve_continues_after_stale_manifest(tmp_path, monkeypatch):
    """放行工单后按产物续跑：验收「放行完就卡住不动」这类真实链路缺陷。"""
    from webapp import supervisor_service as sv, generate_service

    store = _on_store(tmp_path)
    cfg = store.load_config()
    plan = orch.build_plan(store, cfg, mode="auto")
    for key in ("outline", "world"):
        orch.stage_by_key(plan, key)["status"] = "done"
    orch.stage_by_key(plan, "chapters")["status"] = "pending"
    plan["chapters"] = {
        "1": {"status": "done", "step": "done"},
        "2": {"status": "running", "step": "write", "task_id": "r32_ch2"},
        "3": {"status": "pending", "step": "plan_chapter"},
    }
    plan["chapter_cycle"].update({"from": 1, "to": 3, "current": 2,
                                  "current_step": "write"})
    plan["cursor"] = {"node_id": "chapter:2", "chapter": 2, "step": "write"}
    orch.save_plan(store, plan)
    # 第 2 章正文 + 质检都已落盘（工单只是抽检点，不是未完成）
    store._write_json(store.chapter_path(2), {"no": 2, "title": "第2章", "content": "正文"})
    store.save_review_chapter(2, {"chapter": 2, "quality_check": {"passed": True}})
    store._write_json(Path(store.dir) / "task.json",
                      {"task_id": "r32_ch2", "state": "done"})

    calls: list[str] = []
    monkeypatch.setattr(generate_service, "start_agent_run",
                        lambda store_, req, user, internal=None:
                        calls.append((req.agent, req.params.get("ch_no"))
                                     ) or {"run_id": "r3", "task_id": "t3"})
    out = sv.resume(store, {"username": "张阅"}, "r32_ch2")
    assert out["started"] is True
    assert calls == [("writer.chapter", 3)]
    plan = orch.load_plan(store)
    assert plan["chapter_cycle"]["current"] == 3
    assert (plan.get("cursor") or {}).get("node_id") == "chapter:3"


def test_dispatch_repair_counts_and_stops(tmp_path, monkeypatch):
    """派修复必须计数：否则质检一直不过就会无限重派，每轮都是真实扣费。"""
    from webapp import supervisor_service as sv, generate_service

    store = _on_store(tmp_path)
    cfg = store.load_config()
    cfg.supervisor_max_repairs = 3
    store.save_config(cfg)
    plan = orch.build_plan(store, cfg, mode="auto")
    for key in ("outline", "world"):
        orch.stage_by_key(plan, key)["status"] = "done"
    orch.stage_by_key(plan, "chapters")["status"] = "running"
    store._write_json(store.chapter_path(3), {"no": 3, "title": "第3章", "content": "正文"})
    store.save_review_chapter(3, {"chapter": 3, "quality_check": {"passed": False}})
    plan["chapters"] = {"1": {"status": "done", "step": "done"},
                        "2": {"status": "done", "step": "done"},
                        "3": {"status": "running", "step": "review",
                              "attempts": 1, "repairs_used": 0}}
    plan["chapter_cycle"].update({"from": 1, "to": 3, "current": 3, "current_step": "review"})
    plan["cursor"] = {"node_id": "chapter:3", "chapter": 3, "step": "review"}

    review = {"chapter": 3, "quality_check": {"passed": False}}
    action = sv.sup.decide_after_run(plan, "chapter:3", "writer", "ok", cfg, review, [])
    assert action.kind == "repair"

    monkeypatch.setattr(generate_service, "start_agent_run",
                        lambda *a, **kw: {"run_id": "r", "task_id": "t"})
    sv._dispatch(store, plan, action, {"username": "张阅"}, cfg)
    assert plan["budget"]["repairs_used"] == 1
    assert int(plan["chapters"]["3"]["repairs_used"]) == 1

    # 同一章再失败一次：已达 per_chapter 上限，转失败/工单，不再烧钱
    again = sv.sup.decide_after_run(plan, "chapter:3", "writer", "ok", cfg, review, [])
    assert again.kind == "fail" and again.reason_code.startswith("repair_exhausted")

