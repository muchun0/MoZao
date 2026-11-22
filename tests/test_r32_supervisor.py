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
    assert data["next_action"]["kind"] in ("run", "confirm")
    assert not orch.plan_path(store).exists()  # GET 不落盘
    assert data["flow"]["stages"][0]["key"] == "research"


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
