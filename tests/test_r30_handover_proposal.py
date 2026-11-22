# -*- coding: utf-8 -*-
"""R30 M1：交接工单 / 触发器 / 账本提案（P0-5 单测）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import World, Outline
from novel_gen.pipeline import handover, ledger_proposal


def _store(tmp_path: Path, **kwargs) -> ProjectStore:
    cfg = NovelConfig(title="R30交接", chapter_words=500, total_words=2000, **kwargs)
    store = ProjectStore.create(tmp_path / "p-handover", cfg)
    store.save_world(World.from_dict({
        "world_view": "灵气复苏",
        "characters": [{"name": "陆沉", "role": "主角", "desc": "剑修"}],
    }))
    store.save_outline(Outline.from_dict({
        "title": "R30交接",
        "chapters": [{"title": f"第{i}章", "goal": f"目标{i}"} for i in range(1, 5)],
    }))
    return store


def _review(store: ProjectStore, rows: list[dict]) -> None:
    store._write_json(store.review_path(), rows)


def test_hard_rule_hit_creates_blocking_once(tmp_path):
    store = _store(tmp_path, handover_v1=True)
    cfg = store.load_config()
    _review(store, [{"chapter": 2, "issues": [
        {"level": "大改", "type": "设定冲突", "detail": "陆沉突然会火球术"},
    ], "quality_check": {"passed": True, "score": 70}}])
    got = handover.evaluate_after_chapter(store, cfg, "t-h1", 2,
                                          snapshot_ref="runs/t-h1/snapshot.json#step-3")
    assert len(got) == 1 and got[0]["blocking"] and got[0]["severity"] == "high"
    assert got[0]["dedupe_key"] == "ch:2:rule:设定冲突"
    # 同章重复判定 → 复用同一张 open 工单，不新增
    again = handover.evaluate_after_chapter(store, cfg, "t-h1", 2)
    assert again[0]["handover_id"] == got[0]["handover_id"]
    assert len(handover.list_handovers(store, status="", limit=50)) == 1
    assert handover.pending_blocking_for_task(store, "t-h1")[0]["handover_id"] == got[0]["handover_id"]


def test_low_confidence_sampling_marks_auto_passed(tmp_path):
    store = _store(tmp_path, handover_v1=True, handover_sample_rate=0,
                   handover_low_confidence_count=2)
    cfg = store.load_config()
    _review(store, [{"chapter": 1, "issues": [
        {"level": "中改", "type": "节奏", "detail": "中段拖沓"},
        {"level": "小改", "type": "用词", "detail": "重复"},
    ], "quality_check": {"passed": True, "score": 82}}])
    got = handover.evaluate_after_chapter(store, cfg, "t-h2", 1)
    assert got and got[0]["trigger"] == "low_confidence"
    assert got[0]["status"] == handover.AUTO_PASSED and not got[0]["blocking"]
    assert handover.pending_count(store, blocking_only=True) == 0


def test_resolve_writes_audit_and_status(tmp_path):
    store = _store(tmp_path, handover_v1=True)
    cfg = store.load_config()
    _review(store, [{"chapter": 2, "issues": [
        {"level": "大改", "type": "合规", "detail": "越界描写"},
    ], "quality_check": {"passed": True, "score": 60}}])
    hid = handover.evaluate_after_chapter(store, cfg, "t-h3", 2)[0]["handover_id"]
    data = handover.resolve(store, hid, "approve", by="张阅", reason="人工确认可放行")
    assert data["status"] == handover.RESOLVED and data["resolved_by"] == "张阅"
    assert handover.pending_count(store, blocking_only=True) == 0
    audit = (Path(store.dir) / "ledger_audit.jsonl").read_text(encoding="utf-8")
    assert "handover_resolve" in audit and hid in audit


def test_proposal_detect_and_merge(tmp_path):
    store = _store(tmp_path, ledger_proposal=True)
    cfg = store.load_config()
    store.save_chapter_state({"chapter": 2, "characters": {"小九": "跟随"},
                              "items": {"旧钥匙": "在包里"}})
    world_before = json.dumps(store.load_world().to_dict(), ensure_ascii=False, sort_keys=True)
    created = ledger_proposal.detect_new_entities(store, cfg, "t-p1", 2)
    assert len(created) == 2
    assert json.dumps(store.load_world().to_dict(), ensure_ascii=False, sort_keys=True) == world_before
    pid = next(p["proposal_id"] for p in created if "小九" in p["path"])
    data = ledger_proposal.merge_proposal(store, pid, by="张阅")
    assert data["status"] == ledger_proposal.MERGED and data["result_version"]
    world = store.load_world()
    assert any(c.name == "小九" for c in world.characters)
    # 合并后同名提案不再重复生成
    assert ledger_proposal.detect_new_entities(store, cfg, "t-p1", 2) == []


def test_proposal_reject_keeps_artifacts(tmp_path):
    store = _store(tmp_path, ledger_proposal=True)
    cfg = store.load_config()
    store.save_chapter_state({"chapter": 3, "characters": {"阿禾": "登场"}})
    pid = ledger_proposal.detect_new_entities(store, cfg, "t-p2", 3)[0]["proposal_id"]
    before = json.dumps(store.load_world().to_dict(), ensure_ascii=False, sort_keys=True)
    data = ledger_proposal.reject_proposal(store, pid, by="张阅", reason="只是路人")
    assert data["status"] == ledger_proposal.REJECTED
    assert json.dumps(store.load_world().to_dict(), ensure_ascii=False, sort_keys=True) == before
    assert ledger_proposal.list_proposals(store, status=ledger_proposal.OPEN) == []


def test_router_access_first_and_flags_off_404(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from webapp.routers import projects_handovers as r
    from novel_gen.pipeline import task_state

    store = _store(tmp_path)  # 开关默认全关
    calls: list[str] = []
    monkeypatch.setattr(r, "_check_project_access",
                        lambda pid, user: calls.append(pid), raising=False)
    monkeypatch.setattr(r, "_find_project", lambda pid: store, raising=False)
    user = {"id": 1, "username": "张阅"}
    with pytest.raises(HTTPException) as exc:
        r.list_handovers(store.dir.name, "open", "", 50, 0, user)
    assert exc.value.status_code == 404 and calls == [store.dir.name]
    with pytest.raises(HTTPException) as exc:
        r.list_proposals(store.dir.name, "open", 50, 0, user)
    assert exc.value.status_code == 404


def test_router_approve_resumes_and_merge_applies(tmp_path, monkeypatch):
    from webapp.routers import projects_handovers as r
    from novel_gen.pipeline import task_contract, task_state

    store = _store(tmp_path, handover_v1=True, ledger_proposal=True)
    cfg = store.load_config()
    # 造一个待审任务 + 阻塞工单
    task_id = "t-router-1"
    manifest = task_contract.build_manifest(store, task_id, "writer.chapter", {})
    task_state.create_state(store, manifest)
    task_state.transition(store, task_id, task_state.RUNNING)
    task_state.mark_pending(store, task_id, remaining_chapters=[3], reason="rule_hit")
    handover.create_handover(
        store, task_id=task_id, trigger="rule_hit", severity="high", blocking=True,
        evidence={"rule": "设定冲突"}, dedupe_key="ch:2:rule:设定冲突")
    resolved_calls: list[str] = []

    def _fake_resume(store_, task_id_, user_):
        resolved_calls.append(task_id_)
        return {"resumed": True, "task_id": task_id_}

    monkeypatch.setattr(r.generate_service, "resume_pending_run", _fake_resume,
                        raising=False)
    monkeypatch.setattr(r, "_check_project_access", lambda pid, user: None, raising=False)
    monkeypatch.setattr(r, "_find_project", lambda pid: store, raising=False)
    ref = handover.list_handovers(store, status="", limit=10)[-1]["handover_id"]
    out = r.resolve_handover(store.dir.name, ref, r.ResolveReq(action="approve"),
                            {"id": 1, "username": "张阅"})
    assert out["resumed"] is True and resolved_calls == [task_id]

    # 提案合并走路由：真实应用到 world 版本
    store.save_chapter_state({"chapter": 1, "characters": {"小九": "跟随"}, "items": {}})
    pid = ledger_proposal.detect_new_entities(store, store.load_config(), "t-router-2", 1)[0]
    out = r.merge_proposal(store.dir.name, pid["proposal_id"], r.ReasonReq(reason="确认"),
                          {"id": 1, "username": "张阅"})
    assert out["proposal"]["status"] == ledger_proposal.MERGED
    assert any(c.name == "小九" for c in store.load_world().characters)
